import uuid
import tempfile



def create_temp_file() -> str:
    """Create a temporary file with a randomized name"""
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        return temp_file.name
    
def create_temp_file_path() -> str:
    """Returns a temporary file path with a randomized name"""
    return tempfile.gettempdir() + '/' + str(uuid.uuid4())