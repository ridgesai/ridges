import asyncio
import utils.logger as logger

from http import HTTPStatus
from typing import List, Optional
from abc import ABC, abstractmethod
from inference_gateway.models import ModelInfo, InferenceResult, InferenceMessage



NUM_INFERENCE_CHARS_TO_LOG = 30



# my_provider = Provider().init()
class Provider(ABC):
    def __init__(self):
        self.name = None

        self.inference_models = []
        self.embedding_models = []



    # Abstract methods

    @abstractmethod
    async def init(self) -> "Provider":
        pass

    @abstractmethod
    async def _inference(self, model_info: ModelInfo, temperature: float, messages: List[InferenceMessage]) -> InferenceResult:
        pass

    async def inference(self, model_name: str, temperature: float, messages: List[InferenceMessage]) -> InferenceResult:
        # Log the request
        request_first_chars = messages[-1].content.replace('\n', '')[:NUM_INFERENCE_CHARS_TO_LOG] if messages else ''
        logger.info(f"--> Request {self.name}:{model_name} ({sum(len(message.content) for message in messages)} chars): '{request_first_chars}'...")

        response = await self._inference(self.get_inference_model_info_by_name(model_name), temperature, messages)

        # Log the response
        if response.status_code == 200:
            # 200 OK
            response_first_chars = response.response.replace('\n', '')[:NUM_INFERENCE_CHARS_TO_LOG]
            logger.info(f"<-- Response {self.name}:{model_name} ({len(response.response)} chars): '{response_first_chars}'...")
        elif response.status_code != -1:
            # 4xx or 5xx
            logger.warning(f"<-- External Error {self.name}:{model_name}: {response.status_code} {HTTPStatus(response.status_code).phrase}: {response.response}")
        else:
            # -1
            logger.error(f"<-- Internal Error {self.name}:{model_name}: {response.response}")

        return response

    @abstractmethod
    async def _embedding(self, model_info: ModelInfo, input: str) -> List[float]:
        pass



    # Inference

    def is_model_supported_for_inference(self, model_name: str) -> bool:
        return model_name in [model.name for model in self.inference_models]

    def get_inference_model_info_by_name(self, model_name: str) -> Optional[ModelInfo]:
        return next((model for model in self.inference_models if model.name == model_name), None)

    async def test_all_inference_models(self):
        async def test_inference_model(model_name):
            response = await self.inference(
                model_name=model_name,
                temperature=0.5,
                messages=[InferenceMessage(role="user", content="What is 2+2?")]
            )

            return response.status_code == 200
        
        logger.info(f"Testing all {self.name} inference models...")

        tasks = [test_inference_model(model.name) for model in self.inference_models]
        results = await asyncio.gather(*tasks)

        if all(results):
            logger.info(f"Tested all {self.name} inference models")
        else:
            logger.fatal(f"Failed to test {self.name} inference models: {', '.join([model.name for model, result in zip(self.inference_models, results) if not result])}")
        


    # Embedding

    def is_model_supported_for_embedding(self, model_name: str) -> bool:
        return model_name in [model.name for model in self.embedding_models]

    def get_embedding_model_info_by_name(self, model_name: str) -> Optional[ModelInfo]:
        return next((model for model in self.embedding_models if model.name == model_name), None)

    async def embedding(self, model_name: str, input: str) -> List[float]:
        # TODO ADAM
        pass

    async def test_all_embedding_models(self):
        # TODO ADAM
        pass