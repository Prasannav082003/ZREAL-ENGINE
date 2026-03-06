from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
import queue
from threading import Lock
from typing import Dict, Any, Optional
from app.services import auth_service
from app.database import models

def create_status_router(active_jobs: Dict[str, Any], render_queue: queue.Queue, queue_lock: Lock, current_status: Dict[str, Any] = None):
    """
    Creates a router for read-only status endpoints.
    """
    router = APIRouter(tags=["monitoring"])

    @router.get("/engine-status")
    async def get_engine_status(current_user: models.User = Depends(auth_service.get_user_from_api_key)):
        """
        Status Health Check (CRITICAL)
        Returns structured JSON with details about currently running job.
        Optimized for high-frequency polling (O(1) memory access).
        """
        # Optimized path: Return cached status if available
        if current_status is not None:
            return current_status

        with queue_lock:
            # 1. Identify currently Active Job (Processing or Starting)
            active_job_data = None
            is_busy = False
            
            # Search for a job that is currently processing
            for job_id, job_info in active_jobs.items():
                if job_info.get('status') in ['processing', 'starting', 'rendering', 'encoding', 'uploading']:
                    # Found active job
                    active_job_data = {
                        "id": job_id,
                        "status": job_info.get('status'),
                        "step": job_info.get('current_step', 'processing'),
                        "progress": job_info.get('progress', 0),
                        "message": job_info.get('message', ''),
                        "started_at": job_info.get('started_at'),
                        "project_id": job_info.get('project_id'),  # Extra useful info
                        "gallery_id": job_info.get('gallery_id')
                    }
                    is_busy = True
                    break
            
            # If not processing, check if queue has items (still busy technically)
            queue_length = render_queue.qsize()
            if not is_busy and queue_length > 0:
                is_busy = True
                # We could expose the next queued item here if we wanted
            
            # 2. Identify Last Job (for error reporting or history)
            last_job_data = None
            if not active_job_data:
                # Find the most recently modified job that is completed or failed
                finished_jobs = []
                for job_id, job_info in active_jobs.items():
                    if job_info.get('status') in ['completed', 'failed', 'error']:
                        # Use completed_at or failed_at or fallback to queued_at for sorting
                        sort_time = job_info.get('completed_at') or job_info.get('failed_at') or job_info.get('queued_at')
                        finished_jobs.append((sort_time, job_id, job_info))
                
                # Sort by time descending (newest first)
                finished_jobs.sort(key=lambda x: x[0] if x[0] else "", reverse=True)
                
                if finished_jobs:
                    _, last_id, last_info = finished_jobs[0]
                    last_job_data = {
                        "id": last_id,
                        "status": last_info.get('status'),
                        "error": last_info.get('error'), # Only present if failed
                        "failed_at": last_info.get('failed_at'),
                        "completed_at": last_info.get('completed_at'),
                        "message": last_info.get('message')
                    }

        # Construct Response
        response_content = {
            "is_busy": is_busy,
            "queue_length": queue_length,
            "active_job": active_job_data, # Null if not busy
            "last_job": last_job_data      # Useful for error checking when idle
        }

        return JSONResponse(
            status_code=200,
            content=response_content
        )

    @router.get("/queue-status")
    async def get_queue_status(current_user: models.User = Depends(auth_service.get_user_from_api_key)):
        """
        Get the current status of the render queue.
        Read-only endpoint for external monitoring.
        Authentication: X-API-Key required.
        """
        with queue_lock:
            # Create a safe copy of jobs list
            jobs_list = []
            for job_id, job_info in active_jobs.items():
                safe_job = job_info.copy()
                safe_job['job_id'] = job_id
                jobs_list.append(safe_job)
        
        return JSONResponse(
            status_code=200,
            content={
                "queue_size": render_queue.qsize(),
                "active_jobs_count": len(active_jobs),
                "jobs": jobs_list,
                "server_status": "online"
            }
        )

    @router.get("/job-status/{job_id}")
    async def get_job_status(job_id: str, current_user: models.User = Depends(auth_service.get_user_from_api_key)):
        """
        Get the current status of a render job by job ID.
        Authentication: X-API-Key required.
        """
        with queue_lock:
            if job_id not in active_jobs:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
            
            job_info = active_jobs[job_id].copy()
            current_q_size = render_queue.qsize()
        
        # Add current queue size
        job_info['current_queue_size'] = current_q_size
        
        return JSONResponse(
            status_code=200,
            content={
                "job_id": job_id,
                **job_info
            }
        )

    return router
