# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Agentao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.


import argparse
import asyncio
from dataclasses import asdict
from logging import Logger
import random
import bittensor as bt
import copy
import numpy as np
import threading
from enum import Enum
from traceback import print_exception
from typing import List, Union

from agentao.base.neuron import BaseNeuron
from agentao.base.utils.weight_utils import (
    convert_weights_and_uids_for_emit,
)  # TODO: Replace when bittensor switches to numpy
from agentao.helpers.clients import LogContext, LogSessionContext, setup_logger
from agentao.mock import MockDendrite
from agentao.utils.config import add_validator_args
from neurons.constants import LOG_SESSION_CONTEXT

U16_MAX = 65535


def normalize(x, p=2, dim=0):
    norm = np.linalg.norm(x, ord=p, axis=dim, keepdims=True)
    return x / np.clip(norm, 1e-12, None)

class TaskType(Enum):
    """
    Enum for the type of task
    """
    LABELLED_ISSUE = "labelled_issue"
    OPEN_ISSUE = "open_issue"


class BaseValidatorNeuron(BaseNeuron):
    """
    Base class for Bittensor validators. Your validator should inherit from this class.
    """

    neuron_type: str = "ValidatorNeuron"

    @classmethod
    def add_args(cls, parser: argparse.ArgumentParser):
        super().add_args(parser)
        add_validator_args(cls, parser)

    def __init__(self, config=None):
        super().__init__(config=config)

        # Save a copy of the hotkeys to local memory.
        self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)

        # Dendrite lets us send messages to other nodes (axons) in the network.
        if self.config.mock:
            self.dendrite = MockDendrite(wallet=self.wallet)
        else:
            self.dendrite = bt.dendrite(wallet=self.wallet)
        bt.logging.info(f"Dendrite: {self.dendrite}")

        # Set up initial scoring weights for validation
        bt.logging.info("Building validation weights.")
        self.scores = np.zeros(
            self.metagraph.n, dtype=np.float32
        )
        self.pr_scores = np.zeros(
            self.metagraph.n, dtype=np.float32
        )

        # Init sync with the network. Updates the metagraph.
        self.sync()

        # Serve axon to enable external connections.
        if not self.config.neuron.axon_off:
            self.serve_axon()
        else:
            bt.logging.warning("axon off, not serving ip to chain.")

        # Create asyncio event loop to manage async tasks.
        self.loop = asyncio.get_event_loop()

        # Instantiate runners
        self.should_exit: bool = False
        self.is_running: bool = False
        self.thread: Union[threading.Thread, None] = None
        self.lock = asyncio.Lock()

        # Setup logging
        hotkey = self.wallet.hotkey.ss58_address
        log_session_context = LogSessionContext(
            actor_id=hotkey,
            actor_type="validator",
            is_mainnet=self.subtensor.network == "finney",
            log_version=LOG_SESSION_CONTEXT,
            session_id=''.join(random.choices(''.join(map(chr, range(33,127))), k=8)),
            network=self.subtensor.network
        )

        self.logger: Logger = setup_logger(hotkey, log_session_context)

    def serve_axon(self):
        """Serve axon to enable external connections."""

        bt.logging.info("serving ip to chain...")
        try:
            self.axon = bt.axon(wallet=self.wallet, config=self.config)

            try:
                self.subtensor.serve_axon(
                    netuid=self.config.netuid,
                    axon=self.axon,
                )
                bt.logging.info(
                    f"Running validator {self.axon} on network: {self.config.subtensor.chain_endpoint} with netuid: {self.config.netuid}"
                )
            except Exception as e:
                bt.logging.error(f"Failed to serve Axon with exception: {e}")
                pass

        except Exception as e:
            bt.logging.error(f"Failed to create Axon initialize with exception: {e}")
            pass

    async def organic_forward(self):
        raise NotImplementedError

    async def concurrent_forward(self):
        coroutines = [self.organic_forward()]
        coroutines.extend([self.forward() for _ in range(self.config.neuron.num_concurrent_forwards)])
        await asyncio.gather(*coroutines)

    def run(self):
        """
        Initiates and manages the main loop for the miner on the Bittensor network. The main loop handles graceful shutdown on keyboard interrupts and logs unforeseen errors.

        This function performs the following primary tasks:
        1. Check for registration on the Bittensor network.
        2. Continuously forwards queries to the miners on the network, rewarding their responses and updating the scores accordingly.
        3. Periodically resynchronizes with the chain; updating the metagraph with the latest network state and setting weights.

        The essence of the validator's operations is in the forward function, which is called every step. The forward function is responsible for querying the network and scoring the responses.

        Note:
            - The function leverages the global configurations set during the initialization of the miner.
            - The miner's axon serves as its interface to the Bittensor network, handling incoming and outgoing requests.

        Raises:
            KeyboardInterrupt: If the miner is stopped by a manual interruption.
            Exception: For unforeseen errors during the miner's operation, which are logged for diagnosis.
        """

        # Check that validator is registered on the network.
        self.sync()

        bt.logging.info(f"Validator starting at block: {self.block}")

        # This loop maintains the validator's operations until intentionally stopped.
        try:
            while True:
                bt.logging.info(f"step({self.step}) block({self.block})")

                # Run multiple forwards concurrently.
                self.loop.run_until_complete(self.concurrent_forward())

                # Check if we should exit.
                if self.should_exit:
                    break

                # Sync metagraph and potentially set weights.
                self.sync()

                self.step += 1

        # If someone intentionally stops the validator, it'll safely terminate operations.
        except KeyboardInterrupt:
            self.axon.stop()
            bt.logging.success("Validator killed by keyboard interrupt.")
            exit()

        # In case of unforeseen errors, the validator will log the error and continue operations.
        except Exception as err:
            bt.logging.error(f"Error during validation: {str(err)}")
            bt.logging.debug(str(print_exception(type(err), err, err.__traceback__)))

    def run_in_background_thread(self):
        """
        Starts the validator's operations in a background thread upon entering the context.
        This method facilitates the use of the validator in a 'with' statement.
        """
        if not self.is_running:
            bt.logging.debug("Starting validator in background thread.")
            self.should_exit = False
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            self.is_running = True
            bt.logging.debug("Started")

    def stop_run_thread(self):
        """
        Stops the validator's operations that are running in the background thread.
        """
        if self.is_running:
            bt.logging.debug("Stopping validator in background thread.")
            self.should_exit = True
            self.thread.join(5)
            self.is_running = False
            bt.logging.debug("Stopped")

    def __enter__(self):
        self.run_in_background_thread()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Stops the validator's background operations upon exiting the context.
        This method facilitates the use of the validator in a 'with' statement.

        Args:
            exc_type: The type of the exception that caused the context to be exited.
                      None if the context was exited without an exception.
            exc_value: The instance of the exception that caused the context to be exited.
                       None if the context was exited without an exception.
            traceback: A traceback object encoding the stack trace.
                       None if the context was exited without an exception.
        """
        if self.is_running:
            bt.logging.debug("Stopping validator in background thread.")
            self.should_exit = True
            self.thread.join(5)
            self.is_running = False
            bt.logging.debug("Stopped")

    def set_weights(self):
        """
        Sets the validator weights to the metagraph hotkeys based on the scores it has received from the miners. The weights determine the trust and incentive level the validator assigns to miner nodes on the network.
        """

        # Check if self.scores contains any NaN values and log a warning if it does.
        bt.logging.debug("self.scores", type(self.scores))
        if np.isnan(self.scores).any():
            bt.logging.warning(
                f"Scores contain NaN values. This may be due to a lack of responses from miners, or a bug in your reward functions."
            )

        CLOSED_PR_PCT = 0.97
        OPEN_PR_PCT = 0.03
        # Calculate the average reward for each uid across non-zero values.
        # Replace any NaN values with 0.
        # Compute the norm of the scores
        raw_weights_closed = normalize(self.scores, p=1, dim=0) * CLOSED_PR_PCT
        raw_weights_open = normalize(self.pr_scores, p=1, dim=0) * OPEN_PR_PCT

        raw_weights = raw_weights_closed + raw_weights_open

        if raw_weights.shape[0] > self.metagraph.uids.shape[0]:
            bt.logging.warning("More raw_weights than metagraph uids, truncating raw_weights.")
        raw_weights = raw_weights[:self.metagraph.uids.shape[0]]

        # Process the raw weights to final_weights via subtensor limitations.
        try:
            (
                processed_weight_uids,
                processed_weights,
            ) = bt.utils.weight_utils.process_weights_for_netuid(
                uids=self.metagraph.uids,
                weights=raw_weights,
                netuid=self.config.netuid,
                subtensor=self.subtensor,
                metagraph=self.metagraph,
            )
        except Exception as e:
            bt.logging.error(f"Failed to process weights with exception: {e}")
            return
        # Convert to uint16 weights and uids.
        (
            uint_uids,
            uint_weights,
        ) = convert_weights_and_uids_for_emit(
            uids=processed_weight_uids, weights=processed_weights
        )
        bt.logging.debug("uint_weights", uint_weights)
        bt.logging.debug("uint_uids", uint_uids)

        # Set the weights on chain via our subtensor connection.
        result, msg = self.subtensor.set_weights(
            wallet=self.wallet,
            netuid=self.config.netuid,
            uids=uint_uids,
            weights=uint_weights,
            wait_for_finalization=False,
            wait_for_inclusion=False,
            version_key=self.spec_version,
        )
        if result is True:
            float_weights = [float(w) / float(U16_MAX) for w in uint_weights]
            weights_log_info = []
            for uid, weight in zip([int(uid) for uid in uint_uids], float_weights):
                hotkey = self.metagraph.hotkeys[uid]
                weights_log_info.append((hotkey, weight))

            block = self.block

            self.logger.info(f"{block}: {weights_log_info}", extra=asdict(LogContext(
                    log_type="lifecycle",
                    event_type="set_weights",
                )))
        else:
            self.logger.error(f"set_weights failed {msg}")

    def resync_metagraph(self):
        """Resyncs the metagraph and updates the hotkeys and moving averages based on the new metagraph."""
        bt.logging.info("resync_metagraph()")

        # Copies state of metagraph before syncing.
        previous_metagraph = copy.deepcopy(self.metagraph)

        # Sync the metagraph.
        self.metagraph.sync(subtensor=self.subtensor)

        # Check if the metagraph axon info has changed.
        if previous_metagraph.axons == self.metagraph.axons:
            return

        bt.logging.info(
            "Metagraph updated, re-syncing hotkeys, dendrite pool and moving averages"
        )
        # Zero out all hotkeys that have been replaced.
        for uid, hotkey in enumerate(self.hotkeys):
            if hotkey != self.metagraph.hotkeys[uid]:
                self.scores[uid] = 0  # hotkey has been replaced
                self.pr_scores[uid] = 0  # hotkey has been replaced

        # Check to see if the metagraph has changed size.
        # If so, we need to add new hotkeys and moving averages.
        if len(self.hotkeys) < len(self.metagraph.hotkeys):
            # Update the size of the moving average scores.
            new_moving_average = np.zeros((self.metagraph.n))
            min_len = min(len(self.hotkeys), len(self.scores))
            new_moving_average[:min_len] = self.scores[:min_len]
            self.scores = new_moving_average
            self.pr_scores = copy.deepcopy(new_moving_average)

        # Update the hotkeys.
        self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)

    def update_scores(self, rewards: np.ndarray, uids: List[int], task_type: TaskType):
        """Performs exponential moving average on the scores based on the rewards received from the miners."""

        if len(rewards) == 0:
            bt.logging.debug("self.update_scores: Rewards are empty, returning early")
            return

        if len(uids) == 0:
            bt.logging.debug("self.update_scores: Miner UIDs list is empty, returning early")
            return

        if len(rewards) != len(uids):
            self.logger.exception("self.update_scores: Rewards are not the same size as UIDs list (THIS SHOULD NEVER HAPPEN!)")
            return
        
        # Check if rewards contains NaN values.
        if np.isnan(rewards).any():
            bt.logging.warning(f"NaN values detected in rewards: {rewards}")
            # Replace any NaN values in rewards with 0.
            rewards = np.nan_to_num(rewards, nan=0)

        # Check if `uids` is already a numpy array and copy it to avoid the warning.
        if isinstance(uids, np.ndarray):
            uids_tensor = uids.clone().detach()
        else:
            uids_tensor = np.array(uids)

        # Compute forward pass rewards, assumes uids are mutually exclusive.
        # shape: [ metagraph.n ]
        # Update scores with rewards produced by this step.
        # shape: [ metagraph.n ]
        alpha: float = self.config.neuron.moving_average_alpha

        def calculate_scores(old_scores: np.ndarray) -> np.ndarray:
            scattered_rewards = np.copy(old_scores)  # Create a copy to modify
            np.put_along_axis(scattered_rewards, uids_tensor, rewards, axis=0)
            bt.logging.debug(f"Scattered rewards: {rewards}")

            scores = alpha * scattered_rewards + (1 - alpha) * old_scores
            bt.logging.debug(f"New moving avg scores: {scores}")
            return scores

        if task_type == TaskType.LABELLED_ISSUE:
            self.scores = calculate_scores(self.scores)
        elif task_type == TaskType.OPEN_ISSUE:
            self.pr_scores = calculate_scores(self.pr_scores)


    def save_state(self):
        """Saves the state of the validator to a file."""
        bt.logging.info("Saving validator state.")

        # Save the state of the validator to file.
        np.savez_compressed(
            self.config.neuron.full_path + "/state.npz",
            step=self.step,
            scores=self.scores,
            pr_scores=self.pr_scores,
            hotkeys=self.hotkeys,
        )

    def load_state(self):
        """Loads the state of the validator from a file."""
        bt.logging.info("Loading validator state.")

        # Load the state of the validator from file.
        state = np.load(self.config.neuron.full_path + "/state.npz")
        self.step = state["step"]
        self.scores = state["scores"]
        if "pr_scores" in state:
            self.pr_scores = state["pr_scores"]
        else:
            state["pr_scores"] = np.zeros(
                self.metagraph.n, dtype=np.float32
            )
        self.hotkeys = state["hotkeys"]
