from fastapi import APIRouter, HTTPException
import secrets
import string
from datetime import datetime
import os
from dotenv import load_dotenv

from api.src.backend.queries.open_users import get_open_user
from api.src.backend.queries.open_users import create_open_user
from api.src.backend.entities import OpenUser, OpenUserSignInRequest
from loggers.logging_utils import get_logger

load_dotenv()

logger = get_logger(__name__)

sign_in_password = os.getenv("OPEN_USER_SIGN_IN_PASSWORD")

async def open_user_sign_in(request: OpenUserSignInRequest):
    auth0_user_id = request.auth0_user_id
    email = request.email
    name = request.name
    password = request.password

    if password != sign_in_password:
        logger.warning(f"Someone tried to sign in with an invalid password. auth0_user_id: {auth0_user_id}, email: {email}, name: {name}, password: {password}")
        raise HTTPException(status_code=401, detail="Invalid sign in password. Fuck you.")

    logger.info(f"Open user sign in process beginning for: {auth0_user_id}, {email}, {name}")

    existing_user = await get_open_user(auth0_user_id)

    if existing_user:
        logger.info(f"Open user {existing_user.open_hotkey} signed in successfully")
        return {"success": True, "new_user": False, "message": "User exists", "user": existing_user}
    
    new_user = OpenUser(
        open_hotkey="open-" + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(47)),
        auth0_user_id=auth0_user_id,
        email=email,
        name=name,
        registered_at=datetime.now()
    )

    try:
        await create_open_user(new_user)
    except Exception as e:
        logger.error(f"Error creating open user: {e}")
        raise HTTPException(status_code=500, detail="Internal server error. Please try again later and message us on Discord if the problem persists.")
    
    logger.info(f"Open user created: {new_user.open_hotkey}")
    return {"success": True, "new_user": True, "message": "User successfully created", "user": new_user}

router = APIRouter()

routes = [
    ("/sign-in", open_user_sign_in),
]

for path, endpoint in routes:
    router.add_api_route(path, endpoint, tags=["open-users"], methods=["POST"])
