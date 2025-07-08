import logging
import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Initialize the Slack app with your bot token
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))


def start_socket_mode():
    """Start the Slack Socket Mode handler."""
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()


@app.command("/versions")
def handle_versions_command(ack, body, client):
    """Handle the /versions slash command."""
    ack()

    try:
        from .versions import formatted_version_table
        hours = int(body.get("text", "168"))  # 168 hours = 1 week
    except ValueError:
        hours = 168

    response = formatted_version_table(hours)

    client.chat_postMessage(
        channel=body["channel_id"],
        text=response["text"],
        response_type=response["response_type"],
    )


def send_slack_notification(message: str, channel: str = "bot-testing"):
    """Send a notification to Slack as a markdown string"""
    try:
        client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
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
    
    message = f"""
{emoji} *{title}*

🤖 **Agent:** `{agent_name}`
👤 **Miner:** `{short_hotkey}`
📦 **Version:** `{version_num}`
⚡ **Action:** Agent {action}

_Ready for evaluation!_ ✨
"""
    
    return send_slack_notification(message.strip())


def send_evaluation_notification(agent_name: str, miner_hotkey: str, version_num: int, status: str, score: float = None):
    """Send a notification when an evaluation completes."""
    
    short_hotkey = f"{miner_hotkey[:8]}...{miner_hotkey[-8:]}"
    
    if status == "completed" and score is not None:
        if score >= 0.8:
            emoji = "🎉"
            status_text = f"Completed with excellent score: **{score:.2%}**"
        elif score >= 0.6:
            emoji = "✅"
            status_text = f"Completed with good score: **{score:.2%}**"
        elif score >= 0.4:
            emoji = "⚠️"
            status_text = f"Completed with fair score: **{score:.2%}**"
        else:
            emoji = "❌"
            status_text = f"Completed with low score: **{score:.2%}**"
    elif status == "failed":
        emoji = "💥"
        status_text = "Failed during evaluation"
    else:
        emoji = "⏳"
        status_text = f"Status: {status}"
    
    message = f"""
{emoji} *Evaluation Update*

🤖 **Agent:** `{agent_name}`
👤 **Miner:** `{short_hotkey}`
📦 **Version:** `{version_num}`
📊 **Result:** {status_text}
"""
    
    return send_slack_notification(message.strip())


async def send_high_score_notification(agent_name: str, miner_hotkey: str, version_id: str, version_num: int, new_score: float, previous_score: float):
    """Send a notification when an agent achieves a new high score that beats the current approved leader."""
    
    short_hotkey = f"{miner_hotkey[:8]}...{miner_hotkey[-8:]}"
    short_version_id = f"{version_id[:8]}...{version_id[-8:]}"
    score_improvement = new_score - previous_score
    
    message = f"""
:dart: **New Record:** {new_score:.2%} (+{score_improvement:.2%})
:chart_with_upwards_trend: **Previous Best:** {previous_score:.2%}
:robot_face: **Agent:** {agent_name}
:bust_in_silhouette: **Miner:** {short_hotkey}
:package: **Version:** {version_num}
:link: **Version ID:** {short_version_id}
"""
    
    return send_slack_notification(message.strip(), channel="bot-testing") 