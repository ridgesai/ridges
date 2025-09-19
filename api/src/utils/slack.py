from loggers.logging_utils import get_logger
import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
from typing import Optional

# Load environment variables
load_dotenv()

logger = get_logger(__name__)

# Initialize the Slack app with your bot token (only if token is available)
slack_bot_token = os.environ.get("SLACK_BOT_TOKEN")
app = App(token=slack_bot_token) if slack_bot_token else None


def start_socket_mode():
    """Start the Slack Socket Mode handler."""
    if not app:
        logger.info("SLACK_BOT_TOKEN not configured - skipping Socket Mode (this is normal for local development)")
        return
    
    slack_app_token = os.environ.get("SLACK_APP_TOKEN")
    if not slack_app_token:
        logger.info("SLACK_APP_TOKEN not configured - skipping Socket Mode (this is normal for local development)")
        return
    
    handler = SocketModeHandler(app, slack_app_token)
    handler.start()


# Only register the action handler if the app is initialized
if app:
    @app.action("approval_buttons")
    def handle_approval_buttons(ack, body, respond):
        """Handle approval button clicks."""
        ack()
        
        # Get the action details
        action = body["actions"][0]
        action_name = action["name"]
        action_value = action["value"]  # This will be the version_id
        
        if action_name == "approve_version":
            # Handle approve action
            try:
                import asyncio
                from api.src.backend.queries.agents import approve_agent_version
                
                # Run the approval in a separate thread since this is a sync handler
                async def approve_version():
                    return await approve_agent_version(action_value)
                
                # Execute the approval
                result = asyncio.run(approve_version())
                
                # The new approve_agent_version doesn't return a count, it just succeeds or throws
                # So if we get here without exception, it was successful
                # Send ephemeral confirmation to user
                respond(
                    text=f"✅ **Version Approved Successfully!**\n\n🎯 Version `{action_value}` has been approved and set as the current leader.",
                    response_type="ephemeral"
                )
                
                # Update original message to remove button and show approved status
                from slack_sdk import WebClient
                client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
                
                # Get original message info from the interaction
                original_message = body.get("message", {})
                channel_id = body.get("channel", {}).get("id")
                message_ts = original_message.get("ts")
                
                if channel_id and message_ts:
                    # Update the original message to show approved status
                    updated_attachment = original_message.get("attachments", [{}])[0].copy()
                    updated_attachment["color"] = "good"  # Green for approved
                    updated_attachment.pop("actions", None)  # Remove buttons
                    updated_attachment["footer"] = f"✅ APPROVED by <@{body['user']['id']}>"
                    
                    client.chat_update(
                        channel=channel_id,
                        ts=message_ts,
                        text="New Record Approved",
                        attachments=[updated_attachment]
                    )
                    
            except Exception as e:
                respond(
                    text=f"❌ **Approval Failed**\n\nVersion `{action_value}` could not be approved. Error: {str(e)}",
                    response_type="ephemeral"
                )
            



def send_slack_notification(message: Optional[str] = None, channel: str = "bot-testing", blocks: Optional[list] = None, color: Optional[str] = None, approval_version_id: Optional[str] = None):
    """Send a notification to Slack as a markdown string or blocks with optional colored sidebar and approval buttons"""
    
    # Check if Slack is configured - if not, log and return success (for local development)
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.info("SLACK_BOT_TOKEN not configured - skipping Slack notification (this is normal for local development)")
        return True
    
    try:
        logger.debug(f"Activating Slack client.")
        client = WebClient(token=slack_token)
        logger.debug(f"Slack client successfully activated.")
        
        if blocks and color:
            # Use colored attachments for card-like appearance
            logger.debug(f"Attempting to send Slack notification with colored attachments.")
            response = client.chat_postMessage(
                channel=channel,
                text=message or "New notification",  # fallback text for notifications
                attachments=[
                    {
                        "color": color,
                        "blocks": blocks
                    }
                ]
            )
        elif color and message:
            # Use colored attachment with text message and optional approval buttons
            logger.debug(f"Attempting to configure Slack notification with colored attachment and text message.")
            attachment = {
                "color": color,
                "text": message
            }
            
            # Add approval button if version_id is provided (for pending approvals)
            if approval_version_id:
                logger.debug(f"Adding approval button to Slack notification.")
                attachment["callback_id"] = "approval_buttons"  # Required for legacy button actions
                attachment["actions"] = [
                    {
                        "type": "button",
                        "text": "✅ Approve",
                        "name": "approve_version",
                        "value": approval_version_id,
                        "style": "primary"
                    }
                ]
                logger.debug(f"Approval button added to Slack notification.")
            
            logger.debug(f"Attempting to send Slack notification with attachment and text message.")
            response = client.chat_postMessage(
                channel=channel,
                text="New Record Pending Approval",
                attachments=[attachment]
            )
        elif blocks:
            logger.debug(f"Attempting to send Slack notification with blocks.")
            response = client.chat_postMessage(
                channel=channel,
                text=message or "New notification",  # fallback text for notifications
                blocks=blocks,
            )
        else:
            # Existing logic for text messages
            logger.debug(f"Attempting to send Slack notification with text message.")
            response = client.chat_postMessage(
                channel=channel,
                text=message,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
            )
        
        logger.info(f"Slack notification sent successfully: {response['ts']}")
        return True
    except SlackApiError as e:
        logger.error(f"Error sending Slack notification: {str(e)}")
        return False


def send_agent_upload_notification(agent_name: str, miner_hotkey: str, version_num: int, is_new_agent: bool = False):
    """Send a notification when a miner uploads an agent."""
    
    # Truncate hotkey for readability
    short_hotkey = f"{miner_hotkey[:8]}...{miner_hotkey[-8:]}"
    
    if is_new_agent:
        emoji = "🆕"
        action = "created"
        title = "New Agent Created!"
    else:
        emoji = "🔄"
        action = "updated"
        title = "Agent Updated!"
    
    message = f"""```
{emoji} {title}

🤖 Agent: {agent_name}
👤 Miner: {short_hotkey}
📦 Version: {version_num}
⚡ Action: Agent {action}

Ready for evaluation! ✨
```"""
    
    return send_slack_notification(message.strip(), color="good")


def send_evaluation_notification(agent_name: str, miner_hotkey: str, version_num: int, status: str, score: Optional[float] = None):
    """Send a notification when an evaluation completes."""
    
    short_hotkey = f"{miner_hotkey[:8]}...{miner_hotkey[-8:]}"
    
    # Determine color based on status/score
    if status == "completed" and score is not None:
        if score >= 0.8:
            emoji = "🎉"
            status_text = f"Completed with excellent score: {score:.2%}"
            color = "good"
        elif score >= 0.6:
            emoji = "✅"
            status_text = f"Completed with good score: {score:.2%}"
            color = "good"
        elif score >= 0.4:
            emoji = "⚠️"
            status_text = f"Completed with fair score: {score:.2%}"
            color = "warning"
        else:
            emoji = "❌"
            status_text = f"Completed with low score: {score:.2%}"
            color = "danger"
    elif status == "failed":
        emoji = "💥"
        status_text = "Failed during evaluation"
        color = "danger"
    else:
        emoji = "⏳"
        status_text = f"Status: {status}"
        color = "#439FE0"  # Blue for in-progress
    
    message = f"""```
{emoji} Evaluation Update

🤖 Agent: {agent_name}
👤 Miner: {short_hotkey}
📦 Version: {version_num}
📊 Result: {status_text}
```"""
    
    return send_slack_notification(message.strip(), color=color)


async def send_high_score_notification(agent_name: str, miner_hotkey: str, version_id: str, version_num: int, new_score: float, previous_score: float):
    """Send a notification when an agent achieves a new high score that beats the current approved leader."""
    
    score_improvement = new_score - previous_score
    
    # Create a compact formatted message
    message = f"""```
Score: {new_score:.2%} (+{score_improvement:.2%}) | Previous: {previous_score:.2%}
Agent: {agent_name} | Version: {version_num}
Miner: {miner_hotkey}
Version ID: {version_id}
```"""
    
    logger.debug(f"Slack message formatted. Attempting to send Slack notification.")
    
    # Use the hybrid approach: colored sidebar + code block content + approval buttons
    return send_slack_notification(
        message=message, 
        color="warning",  # Use warning/yellow color to indicate pending approval
        approval_version_id=version_id,  # For approval buttons
        channel="bot-testing"
    ) 

async def notify_unregistered_top_miner(miner_hotkey: str):
    if slack_bot_token is None:
        logger.error("Attempted to send Slack notification but SLACK_BOT_TOKEN is not configured")

    try:
        client = WebClient(token=slack_bot_token)
        client.chat_postMessage(
            channel="custom-notifications",
            text=f"WARNING: We just tried to set the weights for hotkey `{miner_hotkey}` but they are not registered on our subnet",
        )
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")

async def notify_unregistered_treasury_hotkey(treasury_hotkey: str):
    if slack_bot_token is None:
        logger.error("Attempted to send Slack notification but SLACK_BOT_TOKEN is not configured")

    try:
        client = WebClient(token=slack_bot_token)
        client.chat_postMessage(
            channel="custom-notifications",
            text=f"WARNING: `{treasury_hotkey}` is not a registered hotkey on our subnet",
        )
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")



async def send_slack_message(text: str, channel: str = "slack-messages"):
    # CXII FIXME
    return
    
    if slack_bot_token is None:
        return

    try:
        client = WebClient(token=slack_bot_token)
        client.chat_postMessage(
            channel=channel,
            text=text,
        )
    except Exception as e:
        logger.error(f"Error in send_slack_message(): {str(e)}")