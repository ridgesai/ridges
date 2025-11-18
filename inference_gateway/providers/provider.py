import asyncio
import utils.logger as logger

from http import HTTPStatus
from typing import List, Optional
from abc import ABC, abstractmethod
from inference_gateway.models import InferenceTool, EmbeddingResult, InferenceResult, InferenceMessage, InferenceToolMode, EmbeddingModelInfo, InferenceModelInfo, InferenceToolParameter, InferenceToolParameterType



NUM_INFERENCE_CHARS_TO_LOG = 30
NUM_EMBEDDING_CHARS_TO_LOG = 30



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
    async def _inference(
        self,
        *,
        model_info: InferenceModelInfo,
        temperature: float,
        messages: List[InferenceMessage],
        tool_mode: InferenceToolMode,
        tools: Optional[List[InferenceTool]]
    ) -> InferenceResult:
        pass

    @abstractmethod
    async def _embedding(
        self,
        *,
        model_info: EmbeddingModelInfo,
        input: str
    ) -> EmbeddingResult:
        pass



    # Inference

    def is_model_supported_for_inference(self, model_name: str) -> bool:
        return model_name in [model.name for model in self.inference_models]

    def get_inference_model_info_by_name(self, model_name: str) -> Optional[InferenceModelInfo]:
        return next((model for model in self.inference_models if model.name == model_name), None)

    async def test_all_inference_models(self):
        async def test_inference_model(model_name):
            response = await self.inference(
                model_name=model_name,
                temperature=0.5,
                messages=[InferenceMessage(role="user", content="Please use the print(str) tool to say something cool.")],
                tool_mode=InferenceToolMode.REQUIRED,
                tools=[InferenceTool(
                    name="print",
                    description="Print a string",
                    parameters=[InferenceToolParameter(
                        name="str",
                        description="The string to print",
                        type=InferenceToolParameterType.STRING
                    )]
                )]
            )

            return response.status_code == 200
        
        logger.info(f"Testing all {self.name} inference models...")

        tasks = [test_inference_model(model.name) for model in self.inference_models]
        results = await asyncio.gather(*tasks)

        if all(results):
            logger.info(f"Tested all {self.name} inference models")
        else:
            logger.fatal(f"Failed to test {self.name} inference models: {', '.join([model.name for model, result in zip(self.inference_models, results) if not result])}")



    async def inference(
        self,
        *,
        model_name: str,
        temperature: float,
        messages: List[InferenceMessage],
        tool_mode: InferenceToolMode = InferenceToolMode.NONE,
        tools: Optional[List[InferenceTool]] = None
    ) -> InferenceResult:
        # Log the request
        request_first_chars = messages[-1].content.replace('\n', '')[:NUM_INFERENCE_CHARS_TO_LOG] if messages else ''
        logger.info(f"--> Inference Request {self.name}:{model_name} ({sum(len(message.content) for message in messages)} char(s)): '{request_first_chars}'...")

        result = await self._inference(
            model_info=self.get_inference_model_info_by_name(model_name),
            temperature=temperature,
            messages=messages,
            tool_mode=tool_mode,
            tools=tools
        )

        # Log the response
        if result.status_code == 200:
            # 200 OK
            result_first_chars = result.output.replace('\n', '')[:NUM_INFERENCE_CHARS_TO_LOG]
            logger.info(f"<-- Inference Response {self.name}:{model_name} ({len(result.output)} char(s)): '{result_first_chars}'...")
        elif result.status_code != -1:
            # 4xx or 5xx
            logger.warning(f"<-- Inference External Error {self.name}:{model_name}: {result.status_code} {HTTPStatus(result.status_code).phrase}: {result.error_message}")
        else:
            # -1
            logger.error(f"<-- Inference Internal Error {self.name}:{model_name}: {result.error_message}")

        return result
        


    # Embedding

    def is_model_supported_for_embedding(self, model_name: str) -> bool:
        return model_name in [model.name for model in self.embedding_models]

    def get_embedding_model_info_by_name(self, model_name: str) -> Optional[EmbeddingModelInfo]:
        return next((model for model in self.embedding_models if model.name == model_name), None)

    async def test_all_embedding_models(self):
        async def test_embedding_model(model_name):
            response = await self.embedding(
                model_name=model_name,
                input="Hello, world!"
            )

            return response.status_code == 200
        
        logger.info(f"Testing all {self.name} embedding models...")

        tasks = [test_embedding_model(model.name) for model in self.embedding_models]
        results = await asyncio.gather(*tasks)

        if all(results):
            logger.info(f"Tested all {self.name} embedding models")
        else:
            logger.fatal(f"Failed to test {self.name} embedding models: {', '.join([model.name for model, result in zip(self.embedding_models, results) if not result])}")

    async def embedding(
        self,
        *,
        model_name: str,
        input: str
    ) -> EmbeddingResult:
        # Log the request
        logger.info(f"--> Embedding Request {self.name}:{model_name} ({len(input)} char(s)): '{input[:NUM_INFERENCE_CHARS_TO_LOG]}'...")

        result = await self._embedding(
            model_info=self.get_embedding_model_info_by_name(model_name),
            input=input
        )

        # Log the response
        if result.status_code == 200:
            # 200 OK
            logger.info(f"<-- Embedding Response {self.name}:{model_name}: {len(result.output)} dimension(s)")
        elif result.status_code != -1:
            # 4xx or 5xx
            logger.warning(f"<-- Embedding External Error {self.name}:{model_name}: {result.status_code} {HTTPStatus(result.status_code).phrase}: {result.error_message}")
        else:
            # -1
            logger.error(f"<-- Embedding Internal Error {self.name}:{model_name}: {result.error_message}")

        return result