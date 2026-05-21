from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


class DuplicateAgentIDError(Exception):
    """Exception raised when a duplicate agent ID is found for a given payment block hash and extrinsic index, indicating that the payment has already been used for another agent upload."""

    def __init__(self, agent_id: "UUID"):
        self.agent_id = agent_id
        super().__init__(f"Agent {agent_id} already exists")
