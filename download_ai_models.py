import os
from pathlib import Path
from huggingface_hub import hf_hub_download
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.getenv("AI_RENDER_MODEL_PATH", str(BASE_DIR / "Realistic_Vision_V5.1_noVAE" / "Realistic_Vision_V5.1.safetensors")))
VAE_PATH = Path(os.getenv("AI_RENDER_VAE_PATH", str(BASE_DIR / "Realistic_Vision_V5.1_noVAE" / "vae-ft-mse-840000-ema-pruned.safetensors")))
MODEL_REPO_ID = os.getenv("AI_RENDER_MODEL_REPO_ID", "SG161222/Realistic_Vision_V5.1_noVAE")
MODEL_FILENAME = os.getenv("AI_RENDER_MODEL_FILENAME", "Realistic_Vision_V5.1.safetensors")
VAE_REPO_ID = os.getenv("AI_RENDER_VAE_REPO_ID", "stabilityai/sd-vae-ft-mse-original")
VAE_FILENAME = os.getenv("AI_RENDER_VAE_FILENAME", "vae-ft-mse-840000-ema-pruned.safetensors")

def download_model():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MODEL_PATH.exists():
        print(f"Downloading model {MODEL_FILENAME} from {MODEL_REPO_ID}...")
        try:
            downloaded = hf_hub_download(repo_id=MODEL_REPO_ID, filename=MODEL_FILENAME, local_dir=str(MODEL_PATH.parent), local_dir_use_symlinks=False)
            if Path(downloaded) != MODEL_PATH:
                Path(downloaded).replace(MODEL_PATH)
            print("Model downloaded successfully.")
        except Exception as e:
            print(f"Failed to download model: {e}")
    else:
        print("Model already exists.")

    if not VAE_PATH.exists():
        print(f"Downloading VAE {VAE_FILENAME} from {VAE_REPO_ID}...")
        try:
            downloaded = hf_hub_download(repo_id=VAE_REPO_ID, filename=VAE_FILENAME, local_dir=str(VAE_PATH.parent), local_dir_use_symlinks=False)
            if Path(downloaded) != VAE_PATH:
                Path(downloaded).replace(VAE_PATH)
            print("VAE downloaded successfully.")
        except Exception as e:
            print(f"Failed to download VAE: {e}")
    else:
        print("VAE already exists.")

if __name__ == "__main__":
    download_model()
