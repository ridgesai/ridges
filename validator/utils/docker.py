import subprocess

from validator.utils.logger import debug, info, warn, error

def build_docker_image(path, image_tag):
    info(f"[SANDBOX] Building Docker image: {image_tag}")
    
    try:
        result = subprocess.run(["docker", "build", "-t", image_tag, path], text=True)
        
        if result.returncode == 0:
            info(f"[SANDBOX] Successfully built Docker image: {image_tag}")
        else:
            raise Exception(f"Docker build failed with exit code {result.returncode}")
            
    except Exception as e:
        error(f"[SANDBOX] Failed to build Docker image: {e}")
        raise