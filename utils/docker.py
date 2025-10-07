import subprocess
import utils.logger as logger



def build_docker_image(dockerfile_dir, tag):
    """
    Build a Docker image.

    Args:
        dockerfile_dir: Path to a directory containing a Dockerfile
        tag: Tag to give the Docker image
    """

    logger.info(f"[SANDBOX] Building Docker image: {tag}")
    
    try:
        result = subprocess.run(["docker", "build", "-t", tag, dockerfile_dir], text=True)
        
        if result.returncode == 0:
            logger.info(f"[SANDBOX] Successfully built Docker image: {tag}")
        else:
            raise Exception(f"Docker build failed with exit code {result.returncode}")
            
    except Exception as e:
        logger.error(f"[SANDBOX] Failed to build Docker image: {e}")
        raise



def get_num_docker_containers():
    """
    Get the number of Docker containers running.
    """

    # This is equivalent to `docker ps -q | wc -l`
    result = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True)
    return len([line for line in result.stdout.strip().split('\n') if line.strip()])