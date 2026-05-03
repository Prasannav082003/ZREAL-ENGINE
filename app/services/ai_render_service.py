import os
import uuid
import torch
import queue
import logging
import json
import time
import requests
from pathlib import Path
from threading import Lock, Thread
from datetime import datetime
from typing import Optional, List, Dict, Any
from PIL import Image, ImageOps
import io

# Try to import AI libraries
try:
    from diffusers import (
        AutoencoderKL,
        ControlNetModel,
        DPMSolverMultistepScheduler,
        StableDiffusionControlNetImg2ImgPipeline,
    )
    from huggingface_hub import hf_hub_download
    from controlnet_aux import CannyDetector
    AI_LIBRARIES_INSTALLED = True
except ImportError:
    AI_LIBRARIES_INSTALLED = False

# Console Colors
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_RESET = "\033[0m"

class AIRenderService:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        
        # AI Cache/State
        self.pipeline = None
        self.canny_detector = None
        self.pipeline_lock = Lock()
        self.setup_lock = Lock()
        self.render_queue = queue.Queue()
        self.worker_thread = None
        self.is_libraries_installed = AI_LIBRARIES_INSTALLED
        self.is_shutting_down = False

        # Device settings
        if self.is_libraries_installed:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.torch_dtype = torch.float16 if self.device == "cuda" else torch.float32
        else:
            self.device = "cpu"
            self.torch_dtype = None

        # Configuration from Environment
        self.input_dir = base_dir / "uploads" / "ai-render" / "input"
        self.output_dir = base_dir / "uploads" / "ai-render" / "output"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.model_path = Path(os.getenv("AI_RENDER_MODEL_PATH", str(base_dir / "Realistic_Vision_V5.1_noVAE" / "Realistic_Vision_V5.1.safetensors")))
        self.vae_path = Path(os.getenv("AI_RENDER_VAE_PATH", str(base_dir / "Realistic_Vision_V5.1_noVAE" / "vae-ft-mse-840000-ema-pruned.safetensors")))
        self.model_repo_id = os.getenv("AI_RENDER_MODEL_REPO_ID", "SG161222/Realistic_Vision_V5.1_noVAE")
        self.model_filename = os.getenv("AI_RENDER_MODEL_FILENAME", "Realistic_Vision_V5.1.safetensors")
        self.vae_repo_id = os.getenv("AI_RENDER_VAE_REPO_ID", "stabilityai/sd-vae-ft-mse-original")
        self.vae_filename = os.getenv("AI_RENDER_VAE_FILENAME", "vae-ft-mse-840000-ema-pruned.safetensors")
        self.controlnet_model_id = os.getenv("AI_RENDER_CONTROLNET_MODEL", "lllyasviel/sd-controlnet-canny")

        self.max_input_size = int(os.getenv("AI_RENDER_MAX_INPUT_SIZE", "1536"))
        self.target_long_side = int(os.getenv("AI_RENDER_TARGET_LONG_SIDE", "3840"))
        self.steps = int(os.getenv("AI_RENDER_STEPS", "60"))
        self.guidance_scale = float(os.getenv("AI_RENDER_GUIDANCE_SCALE", "10.0"))
        self.control_scale = float(os.getenv("AI_RENDER_CONTROL_SCALE", "0.5"))
        self.denoising_strength = float(os.getenv("AI_RENDER_DENOISING_STRENGTH", "0.5"))
        self.clip_skip = int(os.getenv("AI_RENDER_CLIP_SKIP", "2"))
        self.max_file_mb = float(os.getenv("AI_RENDER_MAX_FILE_MB", "20"))
        
        self.storage_connection_string = (
            os.getenv("AI_AZURE_STORAGE_CONNECTION_STRING", "").strip('"')
            or os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip('"')
        )
        self.blob_container = (
            os.getenv("AI_AZURE_CONTAINER_NAME", "").strip()
            or os.getenv("AI_RENDER_BLOB_CONTAINER", "").strip()
            or "ai-images"
        )
        self.status_render_type = os.getenv("AI_RENDER_TYPE", "AI_RENDER")

        self.default_prompt = (
            "masterpiece, photorealistic interior photograph, 8k uhd, dslr, high quality, "
            "vibrant colors, dramatic lighting, high contrast, ray-tracing, mirror-like reflections, "
            "photorealistic sky with soft clouds visible through the window, bright daylight, "
            "physically lit light sources, volumetric lighting, bloom, glare, glowing lamps, "
            "soft photographic glow, cinematic atmosphere, hyper-detailed textures, sunlight rays"
        )
        self.default_negative_prompt = (
            "flat sky, blue plane, cartoon sky, flat color, matte, dull, gray, foggy, "
            "distorted, blurry, low resolution, cartoon, animation, CGI, 3d render, "
            "unreal engine look, stylized, plastic look, overexposed, fake lighting"
        )
        self.prompt = os.getenv("AI_RENDER_PROMPT", self.default_prompt).strip()
        self.negative_prompt = os.getenv("AI_RENDER_NEGATIVE_PROMPT", self.default_negative_prompt).strip()

        # Dependencies (to be injected)
        self.active_jobs = None
        self.queue_lock = None
        self.send_status_update = None
        self.update_global_status = None
        
        self.failed_inputs_dir = base_dir / "failed_inputs"
        self.failed_inputs_dir.mkdir(parents=True, exist_ok=True)
        self.max_render_attempts = 3

    def setup(self, active_jobs, queue_lock, send_status_update, update_global_status):
        """Injected dependencies from main.py"""
        self.active_jobs = active_jobs
        self.queue_lock = queue_lock
        self.send_status_update = send_status_update
        self.update_global_status = update_global_status

        if self.is_libraries_installed:
            print(f"{COLOR_BLUE}--- Initializing AI Render System ---{COLOR_RESET}")
            Thread(target=self.ensure_models_present, daemon=True).start()
            self.ensure_worker_started()

    def ensure_models_present(self):
        self.ensure_ai_model_present()
        self.ensure_ai_vae_present()

    def ensure_ai_model_present(self) -> Path:
        if not self.is_libraries_installed: return self.model_path
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        if self.model_path.exists():
            return self.model_path
        with self.setup_lock:
            if self.model_path.exists():
                return self.model_path
            print(f"{COLOR_YELLOW}AI render model not found locally. Downloading it now...{COLOR_RESET}")
            try:
                downloaded_path = Path(
                    hf_hub_download(
                        repo_id=self.model_repo_id,
                        filename=self.model_filename,
                        local_dir=str(self.model_path.parent),
                        local_dir_use_symlinks=False,
                    )
                )
            except Exception as exc:
                print(f"{COLOR_RED}Failed to download AI render model: {exc}{COLOR_RESET}")
                return self.model_path
            if downloaded_path != self.model_path:
                if self.model_path.exists():
                    self.model_path.unlink()
                downloaded_path.replace(self.model_path)
            print(f"{COLOR_GREEN}AI render model ready: {self.model_path}{COLOR_RESET}")
            return self.model_path

    def ensure_ai_vae_present(self) -> Path:
        if not self.is_libraries_installed: return self.vae_path
        self.vae_path.parent.mkdir(parents=True, exist_ok=True)
        if self.vae_path.exists():
            return self.vae_path
        with self.setup_lock:
            if self.vae_path.exists():
                return self.vae_path
            print(f"{COLOR_YELLOW}AI render VAE not found locally. Downloading it now...{COLOR_RESET}")
            try:
                downloaded_path = Path(
                    hf_hub_download(
                        repo_id=self.vae_repo_id,
                        filename=self.vae_filename,
                        local_dir=str(self.vae_path.parent),
                        local_dir_use_symlinks=False,
                    )
                )
            except Exception as exc:
                print(f"{COLOR_RED}Failed to download AI render VAE: {exc}{COLOR_RESET}")
                return self.vae_path
            if downloaded_path != self.vae_path:
                if self.vae_path.exists():
                    self.vae_path.unlink()
                downloaded_path.replace(self.vae_path)
            print(f"{COLOR_GREEN}AI render VAE ready: {self.vae_path}{COLOR_RESET}")
            return self.vae_path

    def load_pipeline(self):
        if not self.is_libraries_installed: return None
        if self.pipeline is not None and self.canny_detector is not None:
            return self.pipeline
        
        with self.pipeline_lock:
            if self.pipeline is not None and self.canny_detector is not None:
                return self.pipeline
            
            # Dynamic re-check for CUDA
            if self.device == "cpu" and torch.cuda.is_available():
                print(f"{COLOR_YELLOW}  ↳ GPU detected during late initialization. Switching to CUDA...{COLOR_RESET}")
                self.device = "cuda"
                self.torch_dtype = torch.float16

            self.ensure_ai_model_present()
            self.ensure_ai_vae_present()
            
            print(f"{COLOR_BLUE}--- AI Pipeline Initialization Details ---{COLOR_RESET}")
            print(f"    PyTorch Version: {torch.__version__}")
            print(f"    Target Device: {self.device} / Dtype: {self.torch_dtype}")
            print(f"    CUDA Available (Runtime): {torch.cuda.is_available()}")
            if torch.cuda.is_available():
                print(f"    Device Name: {torch.cuda.get_device_name(0)}")
            print(f"    Model Path: {self.model_path}")
            print(f"    VAE Path: {self.vae_path}")
            print(f"{COLOR_BLUE}----------------------------------------{COLOR_RESET}")
            
            controlnet = ControlNetModel.from_pretrained(self.controlnet_model_id, torch_dtype=self.torch_dtype).to(self.device)
            vae = AutoencoderKL.from_single_file(str(self.vae_path), torch_dtype=self.torch_dtype).to(self.device)
            pipeline = StableDiffusionControlNetImg2ImgPipeline.from_single_file(
                str(self.model_path),
                controlnet=controlnet,
                vae=vae,
                torch_dtype=self.torch_dtype,
                safety_checker=None,
                local_files_only=False,
            )
            pipeline = pipeline.to(self.device)
            pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
                pipeline.scheduler.config,
                algorithm_type="dpmsolver++",
                solver_order=2,
                use_karras_sigmas=True,
            )
            if self.device == "cuda":
                try:
                    pipeline.enable_xformers_memory_efficient_attention()
                    pipeline.enable_vae_slicing()
                    pipeline.enable_vae_tiling()
                    # FreeU: Enhances texture and contrast/details
                    pipeline.enable_freeu(s1=0.9, s2=0.2, b1=1.2, b2=1.4)
                except Exception as e:
                    print(f"    ⚠️ Optimization warning: {e}")
            
            torch.backends.cuda.matmul.allow_tf32 = True
            
            self.canny_detector = CannyDetector()
            self.pipeline = pipeline
            return self.pipeline

    def resize_for_generation(self, image: Image.Image, max_size: Optional[int] = None) -> Image.Image:
        if max_size is None:
            max_size = self.max_input_size
        width, height = image.size
        scale = min(max_size / max(width, height), 1.0)
        resized_width = max(8, (int(width * scale) // 8) * 8)
        resized_height = max(8, (int(height * scale) // 8) * 8)
        return image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)

    def prepare_control_image(self, image: Image.Image) -> Image.Image:
        # Pre-process image with significantly stronger blur to resolve 'squeezing/jagged' edges
        # This merges aliased Godot pixels into smooth, continuous lines for the AI to follow.
        from PIL import ImageFilter
        smoothed_image = image.filter(ImageFilter.GaussianBlur(radius=1.5))
        
        # Use explicit thresholds to ignore minor noise and only catch structural edges
        control_image = self.canny_detector(smoothed_image, low_threshold=100, high_threshold=200)
        
        if not isinstance(control_image, Image.Image):
            control_image = Image.fromarray(control_image)
        
        control_image = control_image.convert("RGB")
        if control_image.size != image.size:
            control_image = control_image.resize(image.size, Image.Resampling.LANCZOS)
        return control_image

    def resize_for_delivery(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        long_side = max(width, height)
        if long_side >= self.target_long_side:
            return image
        scale = self.target_long_side / long_side
        output_width = max(8, int(width * scale))
        output_height = max(8, int(height * scale))
        return image.resize((output_width, output_height), Image.Resampling.LANCZOS)

    def render_image(self, source_image: Image.Image) -> Image.Image:
        pipeline = self.load_pipeline()
        if self.canny_detector is None:
            raise RuntimeError("AI render detector not initialized")
        
        # --- STAGE 1: Primary Generation ---
        # Resize to generation resolution (e.g. 1536)
        prepared_image = self.resize_for_generation(ImageOps.exif_transpose(source_image.convert("RGB")))
        control_image = self.prepare_control_image(prepared_image)
        
        print(f"{COLOR_BLUE}🚀 Starting Two-Pass Quality Render ({prepared_image.width}x{prepared_image.height})...{COLOR_RESET}")
        
        with self.pipeline_lock:
            # Pass 1: Global coherence and layout
            rendered = pipeline(
                prompt=f"{self.prompt}, high resolution interior",
                negative_prompt=self.negative_prompt,
                image=prepared_image,
                control_image=control_image,
                height=prepared_image.height,
                width=prepared_image.width,
                strength=self.denoising_strength,
                num_inference_steps=self.steps,
                guidance_scale=self.guidance_scale,
                controlnet_conditioning_scale=self.control_scale,
                clip_skip=self.clip_skip,
            ).images[0]
            
            # --- STAGE 2: Refinement Pass (Hi-Res Fix style) ---
            print(f"{COLOR_BLUE}✨ Polishing and Refinement...{COLOR_RESET}")
            # Slightly higher resolution for refinement if we're not already at target
            refine_size = min(int(self.max_input_size * 1.25), self.target_long_side)
            refine_image = self.resize_for_generation(rendered, max_size=refine_size)
            
            # Use a lower strength (0.25) to add detail without changing layout
            rendered = pipeline(
                prompt=f"{self.prompt}, detailed textures, sharp focus, hyperrealistic",
                negative_prompt=self.negative_prompt,
                image=refine_image,
                control_image=self.prepare_control_image(refine_image),
                height=refine_image.height,
                width=refine_image.width,
                strength=0.25, 
                num_inference_steps=int(self.steps * 0.4), # Fewer steps for refinement
                guidance_scale=self.guidance_scale,
                controlnet_conditioning_scale=self.control_scale * 0.8, # Lower control for more creative detail
                clip_skip=self.clip_skip,
            ).images[0]
            
        return self.resize_for_delivery(rendered)

    def upload_to_blob(self, file_path: str, blob_container: Optional[str] = None) -> Optional[str]:
        # Implementation moved from main.py
        container_name = (
            blob_container
            or os.getenv("AI_AZURE_CONTAINER_NAME", "").strip()
            or os.getenv("AI_RENDER_BLOB_CONTAINER", "").strip()
            or os.getenv("AZURE_CONTAINER_NAME", "render-images")
        )
        conn_str = self.storage_connection_string

        if conn_str:
            try:
                from azure.storage.blob import BlobServiceClient, ContentSettings
            except ImportError as e:
                print(f"{COLOR_YELLOW}⚠️  Azure SDK not installed: {e}. Falling back to main upload if available.{COLOR_RESET}")
            else:
                try:
                    filename = os.path.basename(file_path)
                    with open(file_path, "rb") as f:
                        data = f.read()
                    blob_service_client = BlobServiceClient.from_connection_string(conn_str)
                    blob_client = blob_service_client.get_blob_client(container=container_name, blob=filename)
                    blob_client.upload_blob(data, overwrite=True, content_settings=ContentSettings(content_type="image/png"))
                    return blob_client.url
                except Exception as e:
                    print(f"{COLOR_YELLOW}⚠️  Azure SDK upload failed: {e}.{COLOR_RESET}")

        return None

    def save_failed_input(self, job_data: dict, error_msg: str, attempt: int):
        try:
            user_id = job_data.get('user_id', 'unknown')
            project_id = job_data.get('project_id', 'unknown')
            gallery_id = job_data.get('gallery_id', 'unknown')
            job_id = job_data.get('job_id', 'unknown')
            job_type = job_data.get('type', 'unknown')
            timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')

            base_name = f"user_{user_id}_project_{project_id}_{timestamp_str}"
            input_json_path = self.failed_inputs_dir / f"{base_name}_input.json"
            
            payload_obj = job_data.get('payload')
            payload_dict = {}
            if payload_obj is not None:
                try:
                    payload_dict = payload_obj.dict() if hasattr(payload_obj, 'dict') else payload_obj
                except Exception:
                    payload_dict = str(payload_obj)
            
            with open(input_json_path, 'w', encoding='utf-8') as f:
                json.dump(payload_dict, f, indent=2, ensure_ascii=False, default=str)

            error_txt_path = self.failed_inputs_dir / f"{base_name}_error.txt"
            with open(error_txt_path, 'w', encoding='utf-8') as f:
                f.write(f"Timestamp      : {datetime.now().isoformat()}\n")
                f.write(f"Job ID         : {job_id}\n")
                f.write(f"Error          : {error_msg}\n")
                f.write(f"Attempt        : {attempt}\n")

            print(f"{COLOR_YELLOW}📁 Failed input saved to: {self.failed_inputs_dir}{COLOR_RESET}")
        except Exception as save_err:
            print(f"{COLOR_RED}⚠️  Could not save failed input: {save_err}{COLOR_RESET}")

    def background_task_main(self, job_data: dict):
        job_id = job_data["job_id"]
        try:
            with self.queue_lock:
                if job_id in self.active_jobs:
                    self.active_jobs[job_id]["status"] = "processing"
                    self.active_jobs[job_id]["progress"] = 20
                    self.active_jobs[job_id]["render_type"] = self.status_render_type
                    self.active_jobs[job_id]["type"] = "ai render"
                    self.active_jobs[job_id]["current_step"] = "rendering"
                    self.active_jobs[job_id]["started_at"] = datetime.now().isoformat()
            
            self.send_status_update(
                project_id=job_data["project_id"],
                gallery_id=job_data["gallery_id"],
                user_id=job_data["user_id"],
                job_id=job_id,
                status="processing",
                step="rendering",
                progress=20,
                message="AI render is processing your image.",
                render_type=self.status_render_type,
                wait_for_delivery=True
            )
            
            source_image = Image.open(job_data["input_path"]).convert("RGB")
            rendered_image = self.render_image(source_image)
            rendered_image.save(job_data["output_path"], format="PNG")
            
            with self.queue_lock:
                if job_id in self.active_jobs:
                    self.active_jobs[job_id]["progress"] = 80
            
            self.send_status_update(
                project_id=job_data["project_id"],
                gallery_id=job_data["gallery_id"],
                user_id=job_data["user_id"],
                job_id=job_id,
                status="processing",
                step="uploading",
                progress=80,
                message="Uploading rendered image.",
                render_type=self.status_render_type,
                wait_for_delivery=True
            )
            
            blob_url = self.upload_to_blob(job_data["output_path"], blob_container=self.blob_container)
            if not blob_url and hasattr(self, 'upload_to_blob_fallback') and self.upload_to_blob_fallback:
                # Optional fallback to main.py's upload function if SAS is needed
                blob_url = self.upload_to_blob_fallback(job_data["output_path"], blob_container=self.blob_container)
                
            if not blob_url: raise RuntimeError("Blob upload failed")
            
            with self.queue_lock:
                if job_id in self.active_jobs:
                    self.active_jobs[job_id]["status"] = "completed"
                    self.active_jobs[job_id]["progress"] = 100
                    self.active_jobs[job_id]["image_url"] = blob_url
                    self.active_jobs[job_id]["current_step"] = "completed"
                    self.active_jobs[job_id]["completed_at"] = datetime.now().isoformat()
            
            self.send_status_update(
                project_id=job_data["project_id"],
                gallery_id=job_data["gallery_id"],
                user_id=job_data["user_id"],
                job_id=job_id,
                status="completed",
                step="completed",
                progress=100,
                message="AI render completed successfully.",
                blob_url=blob_url,
                render_type=self.status_render_type,
                wait_for_delivery=True,
                details={
                    "image_url": blob_url,
                    "output_filename": os.path.basename(job_data["output_path"]),
                    "render_type": self.status_render_type,
                }
            )
        except Exception as e:
            error_msg = str(e)
            with self.queue_lock:
                if job_id in self.active_jobs:
                    self.active_jobs[job_id]["status"] = "failed"
                    self.active_jobs[job_id]["error"] = error_msg
                    self.active_jobs[job_id]["current_step"] = "failed"
                    self.active_jobs[job_id]["failed_at"] = datetime.now().isoformat()
            
            self.send_status_update(
                project_id=job_data["project_id"],
                gallery_id=job_data["gallery_id"],
                user_id=job_data["user_id"],
                job_id=job_id,
                status="error",
                step="error",
                progress=0,
                message=f"AI render failed: {error_msg}",
                render_type=self.status_render_type,
                wait_for_delivery=True
            )

    def worker_loop(self):
        print(f"{COLOR_GREEN}AI Render Worker Started{COLOR_RESET}")
        while not self.is_shutting_down:
            try:
                job_data = self.render_queue.get(timeout=1)
                if job_data is None: break
                self.background_task_main(job_data)
                self.render_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"{COLOR_RED}AI worker error: {e}{COLOR_RESET}")

    def ensure_worker_started(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.worker_thread = Thread(target=self.worker_loop, daemon=True, name="ai-render-worker")
        self.worker_thread.start()

    def shutdown(self):
        self.is_shutting_down = True
        try:
            self.render_queue.put(None)
        except:
            pass

# Create a singleton instance
# AIRenderService will be initialized in main.py
ai_render_service = None

def get_ai_render_service(base_dir: Path = None) -> AIRenderService:
    global ai_render_service
    if ai_render_service is None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent.parent
        ai_render_service = AIRenderService(base_dir)
    return ai_render_service
