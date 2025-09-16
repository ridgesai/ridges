from new_validator.connection import ConnectionManager


class EvaluationManager():
    connection_manager: ConnectionManager

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self.connection_manager = connection_manager

    def create_evaluation_manager():
        # Begin by cleanup of docker containers
        pass

    def _cleanup_docker_container():
        pass 

    def _send_heartbeat():
        pass

    def handle_evaluation_request():
        pass 

    def shutdown_evaluations():
        pass
    