from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


class DuplicateAgentIDError(Exception):
    """Exception raised when a duplicate agent ID is found for a given payment block hash and extrinsic index, indicating that the payment has already been used for another agent upload."""

    def __init__(self, agent_id: "UUID"):
        self.agent_id = agent_id
        super().__init__(f"Agent {agent_id} already exists")


class ColdkeyBannedError(Exception):
    """Raised when an agent insert loses a race with a coldkey ban."""

    def __init__(self, miner_coldkey: str):
        self.miner_coldkey = miner_coldkey
        super().__init__(f"Coldkey {miner_coldkey} is banned")


class UploadCreditUnavailableError(Exception):
    """Raised when a credit does not exist or cannot be used by this hotkey."""


class UploadCreditAlreadyRedeemedError(Exception):
    """Raised when a credit was redeemed for different agent source."""

    def __init__(self, agent_id: "UUID"):
        self.agent_id = agent_id
        super().__init__(f"Upload credit was already redeemed for agent {agent_id}")


class UploadFundingConflictError(Exception):
    """Raised when a burn reservation does not exactly match the verified funding."""


class UploadCooldownError(Exception):
    """Raised when a miner is still inside a competition's upload cooldown."""

    def __init__(self, latest_created_at):
        self.latest_created_at = latest_created_at
        super().__init__("Upload cooldown has not elapsed")


class CompetitionNotAcceptingSubmissionsError(Exception):
    """Raised when the authoritative current competition cannot accept a new agent."""

    def __init__(self, set_id: int | None, state: str | None):
        self.set_id = set_id
        self.state = state
        if set_id is None:
            message = "No competition has started"

        elif state is None:
            message = f"Competition {set_id} has no initialized policy"

        else:
            message = f"Competition {set_id} is {state} and is not accepting submissions"
        super().__init__(message)


class AgentCompetitionMembershipMismatchError(Exception):
    """Raised when work is requested in a set other than the agent's competition."""

    def __init__(self, agent_id: "UUID", agent_set_id: int, requested_set_id: int):
        self.agent_id = agent_id
        self.agent_set_id = agent_set_id
        self.requested_set_id = requested_set_id
        super().__init__(f"Agent {agent_id} belongs to set {agent_set_id}, not requested set {requested_set_id}")


class EvaluationSetMembershipMismatchError(AgentCompetitionMembershipMismatchError):
    """Raised when evaluation issuance tries to override an agent's competition."""


class CompetitionNotFoundError(Exception):
    """Raised when an admin mutation targets a competition that does not exist."""

    def __init__(self, set_id: int):
        self.set_id = set_id
        super().__init__(f"Competition {set_id} does not exist")


class CompetitionAdminConflictError(Exception):
    """Raised when a requested competition target is invalid for current state."""
