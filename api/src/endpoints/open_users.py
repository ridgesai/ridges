from fastapi import APIRouter, HTTPException
import secrets
import string
from datetime import datetime
import os
from dotenv import load_dotenv

from api.src.backend.queries.open_users import get_open_user, check_open_user_email_in_whitelist, create_open_user, add_open_user_email_to_whitelist, get_open_user_by_email
from api.src.backend.entities import OpenUser, OpenUserSignInRequest
from loggers.logging_utils import get_logger

load_dotenv()

logger = get_logger(__name__)

open_user_password = os.getenv("OPEN_USER_PASSWORD")
print(open_user_password)

async def open_user_sign_in(request: OpenUserSignInRequest):
    auth0_user_id = request.auth0_user_id
    email = request.email
    name = request.name
    password = request.password

    if password != open_user_password:
        logger.warning(f"Someone tried to sign in with an invalid password. auth0_user_id: {auth0_user_id}, email: {email}, name: {name}, password: {password}")
        raise HTTPException(status_code=401, detail="Invalid sign in password. Fuck you.")
    
    try:
        is_email_in_whitelist = await check_open_user_email_in_whitelist(email)
    except Exception as e:
        logger.error(f"Error checking if email {email} is in whitelist: {e}")
        raise HTTPException(status_code=500, detail="Internal server error. Please try again later and message us on Discord if the problem persists.")
    
    if not is_email_in_whitelist:
        raise HTTPException(status_code=401, detail="Email not in whitelist. Please contact us on Discord to get access to upload agents.")

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

async def add_email_to_whitelist(email: str, password: str):
    if password != open_user_password:
        logger.warning(f"Someone tried to add an email to the whitelist with an invalid password. email: {email}, password: {password}")
        raise HTTPException(status_code=401, detail="Invalid whitelist password. Fuck you.")

    try:
        await add_open_user_email_to_whitelist(email)
    except Exception as e:
        logger.error(f"Error adding email {email} to whitelist: {e}")
        raise HTTPException(status_code=500, detail="Internal server error. Please try again later and message us on Discord if the problem persists.")
    
    return {"success": True, "message": "Email added to whitelist", "email": email}

async def get_user_by_email(email: str, password: str):
    if password != open_user_password:
        logger.warning(f"Someone tried to get user by email with an invalid password. email: {email}, password: {password}")
        raise HTTPException(status_code=401, detail="Invalid password. Fuck you.")

    try:
        user = await get_open_user_by_email(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {"success": True, "user": user}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user by email {email}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error. Please try again later and message us on Discord if the problem persists.")

router = APIRouter()

routes = [
    ("/sign-in", open_user_sign_in),
    ("/add-email-to-whitelist", add_email_to_whitelist),
    ("/get-user-by-email", get_user_by_email),
]

for path, endpoint in routes:
    router.add_api_route(path, endpoint, tags=["open-users"], methods=["POST"])
