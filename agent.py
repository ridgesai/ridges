from __future__ import annotations
import ast
import json
import os
import requests
import subprocess
import ast, sys
import textwrap
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from json import JSONDecodeError
import re
import inspect
import random
from enum import Enum
import json
import csv
import logging
import unittest

REJECTION_FEEDBACK_PROMPT = f"ERROR: Duplicate tool call detected - You've attempted this exact tool call ({{next_tool_name}}) with identical arguments multiple times in the last few steps. Consecutive rejections: {{consecutive_rejections}}\n\n" \
                            "This indicates you're stuck in a loop. The same action with the same inputs will produce the same result.\n\n" \
                            "Please try a different approach:\n" \
                            "1. Use different arguments or parameters\n" \
                            "2. Try a different tool that might provide the information you need\n" \
                            "3. Re-examine the previous outputs - you may already have the information\n" \
                            "4. Consider if the problem requires a fundamentally different solution strategy\n"

STOP_INSTRUCTION=textwrap.dedent("""
# 🎨 
DO NOT generate `observation:` in your response. It will be provided by user for you.
Generate only SINGLE triplet of `next_thought`, `next_tool_name`, `next_tool_args` in your response.
""")


FORMAT_PROMPT_V0=textwrap.dedent("""
**📝 Response Format Requirements**

1. **Strict Triplet Format**:
   - `next_thought`: Detailed reasoning (include:
     - Problem understanding
     - Code analysis
     - Solution justification
     - Validation plan)
   - `next_tool_name`: Must be an exact tool name from the tool list
   - `next_tool_args`: Valid JSON with:
     - Proper escaping
     - No trailing commas
     - Tool-specific parameters

2. **Error Handling Format**:
   - For errors: 
     next_thought: "Error: [detailed explanation]"
     next_tool_name: ""
     next_tool_args: {}

3. **Example Valid Format**:
   next_thought: "I'll fix the JSON parsing issue by adding proper error handling and validation"
   next_tool_name: "apply_code_edit"
   next_tool_args: {
     "file_path": "network.py",
     "search": "return json.loads(response)",
     "replace": "try:\n    return json.loads(response)\nexcept JSONDecodeError:\n    logger.error(f'Invalid JSON: {{response}}')\n    raise"
   }

4. **Invalid Format Examples** (Avoid These):
   - Missing any of the three required fields
   - JSON syntax errors in next_tool_args
   - Extra text outside the triplet format
   - Using incorrect tool names
   - Not quoting special characters properly
""")


PROBLEM_TYPE_CREATE = "CREATE"
PROBLEM_TYPE_FIX = "FIX"

# Assuming Python only
PYTHON_EXTENSIONS = ["py"]

TEST_CODE_GENERATION_TIMEOUT = 400
TEST_CODE_GENERATION_MAX_STEPS = 100
CREATE_TASK_SOLVE_MAX_STEPS = 300

PROBLEM_TYPE_CHECK_PROMPT = textwrap.dedent(
    '''
    You are the problem type checker that will categories problem type into:
    
    1. CREATE: If the problem statement is about creating a new functionality from scratch.
    2. FIX: If the problem statement is about fixing a bug, creating a new functionality or improving the existing codebase.
    
    Only respond with the "FIX" or "CREATE".
    '''
)

FIX_TASK_SYSTEM_PROMPT = textwrap.dedent("""
# Hey there! You're a Coding Assistant 🚀. I have uploaded all files of a python repository. Your current working directory is at the root of that repo. You will be provided with a problem statement and you need to make the necessary changes to fix the issue.

## Follow these steps to fix the issue:
1. As a first step, find the relevant files in the repo to work on.
2. Localise the code causing the issue.
3. Edit the sourcecode of the repo to resolve the issue.
4. Think about edgecases and make sure the fix handles them as well.
5. Code must always be backward compatible unless explicitly mentioned otherwise in the problem statement.
6. Thoroughly check the entire code base to ensure the changes made are exhaustive and does not break any other functionality.
7. Thoroughly check the entire code base to ensure the changes user requested are only limited to the ones you have identified.
8. Never edit/update the existing test files.
9. Do not create any new files or directories.
10. Always check all the test cases which will be impacted with your change and ensure they don't fail.
11. You need to look at both expected output mentioned in the problem statement AND the output in the most relevant test case. This is very important.
12. If you find that the error while running the run_code tool due to missing dependencies, do not try to solve it as you don't have any internet access.
13. When test failures show error message string mismatches (different capitalization, word order, or phrasing), you MUST update your implementation code to use the exact error message format from the failing test. Do not modify the tests - fix your code's error messages to match what the tests expect.
14. You must finish EVERYTHING before calling the finish tool. Do not call it for validation on proposals, or for feedback. Only call it once you have implemented the solution and verified that it works.

You have access to the following tools:-
{tools_docs}

{format_prompt}
""")

FIX_TASK_INSTANCE_PROMPT_TEMPLATE = textwrap.dedent("""
# Now let's start. Here is the problem statement:
{problem_statement}
""")

TEST_CODE_GENERATION_SYSTEM_PROMPT = textwrap.dedent(
    '''
    You are the Python expert and your role is to create a test function from the given test cases, by following the below steps:
    - Read the codebase to understand existing files, and function usages
    - Implement the given test cases into a test file following the example structure using unittest.TestCase class:
        ```
        import unittest
    
        from main import (
            func_a
        )
    
        class TestFuncA(unittest.TestCase):
            def test_func_a(self):
                self.assertEqual(func_a(), "expected_output")
        ```
    
    Important Things:
    - Start by creating an empty test file using `create_test_file` tool.
    - Implement one test case at one step using `apply_code_edit` tool.
    - Make sure all test cases are implemented.
    - **Don't create a whole test code** at one step because you have a context limit.
    
    You have access to the following tools:
    {tools_docs}
    
    {format_prompt}
    '''
)

TEST_CASE_EXTRACTION_SYSTEM_PROMPT = textwrap.dedent(
    '''
    You are the test case generator that will generate test cases for the given problem statement and expected input & output format, by following the below steps:
    - Undestand the problem statement completely
    - Understand the expected input and output format from the given code skeleton
    - Extract all the test cases mentioned in problem statement
    
    Important Notes:
    - Minize each test case as smaller as possible
    - Don't miss any test cases
    - **DO NOT GENERATE** any test cases not mentioned in problem statement.
    
    Only respond with the test cases in JSON format.
    Response Example:
    
    [
        {{
            "input": "add(a=1, b=2)",
            "output": "3",
            "test_case_description": "Add two numbers"
        }}
    ]
    '''
)

CREATE_TASK_SYSTEM_PROMPT = textwrap.dedent(
    '''
    You are a senior Python expert tasked with writing a code that resolves the problem statement given by the user.
    You're provided with:
    1. The **problem statement**.
    2. The **test file** which you should pass after writing a code.
    
    # Steps to follow:
    - Analyze the problem statement carefully to understand the user exepctation
    - Check the test file to understand edge cases you need to implement
    - **CRITICAL**: When implementing error messages or exception handling, read the test file FIRST to see the EXACT error message strings expected (including exact capitalization, word order, and phrasing). Do NOT guess or use the problem statement's examples - use the test file as the source of truth.
    - Implement the solution one by one, using tools provided.
    - Finish the task with "finish" tool.
    - If "finish" tool returns verification errors, you should fix them before you can finish the workflow.

    # Important Things:
    - When you use `apply_code_edit`, **make search and replace string as minimal as possible because you have context limit**
    - Do not remove any existing debug prints in the original file.
    - If you want to debug, add debug prints to main file or even test file where you can track the issue with print("DEBUG: <message>") or print(f"DEBUG: <message> {{<variable>}}").
    - CRITICAL: Error messages in your implementation MUST match the exact strings in test assertions character-by-character. Check test files for assertEqual/assertRaises assertions to see the exact expected format. Problem statements often show different formats than tests expect.
    
    
    You have access to the following tools:
    {tools_docs}
    
    {format_prompt}
    ''')


DEFAULT_PROXY_URL = os.getenv("SANDBOX_PROXY_URL", "http://sandbox_proxy")
DEFAULT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "2000"))
MAX_TEST_PATCH_TIMEOUT = int(os.getenv("MAX_STEPS_TEST_PATCH_FIND", "400"))

GLM_MODEL_NAME = "zai-org/GLM-4.5-FP8"
KIMI_MODEL_NAME = "moonshotai/Kimi-K2-Instruct"
DEEPSEEK_MODEL_NAME = "deepseek-ai/DeepSeek-V3-0324"
QWEN_MODEL_NAME = "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"
AGENT_MODELS=[GLM_MODEL_NAME, KIMI_MODEL_NAME, DEEPSEEK_MODEL_NAME, QWEN_MODEL_NAME]
MAX_FIX_TASK_STEPS = 400

DEBUG_MODE=True

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

for h in list(logger.handlers):
    logger.removeHandler(h)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)
run_id=None

class EnhancedCOT:
    class Action:

        def __init__(self, next_thought: str, next_tool_name: str, next_tool_args: dict, observation: list|tuple|str,is_error:bool=False,raw_response:str=None,total_attempts:int=0,inference_error_counter:dict=None,request_data:list=None):
            self.next_thought=next_thought
            self.next_tool_name=next_tool_name
            self.next_tool_args=next_tool_args
            self.observation=";".join(observation) if isinstance(observation,list) else observation
            self.is_error=is_error
            self.raw_response=raw_response
            self.total_attempts=total_attempts
            self.inference_error_counter=inference_error_counter
            self.request_data=request_data
            self.is_deleted=False
    def __init__(self,latest_observations_to_keep=5):
        self.thoughts: list[EnhancedCOT.Action] = []
        self.latest_observations_to_keep=latest_observations_to_keep

    def is_valid_tool_call(self, next_tool_name: str|list, next_tool_args: dict|list) -> tuple[bool, str]:
        """
        Check if a tool call is valid (not a duplicate).
        Returns (is_valid, reason_if_invalid)
        """
        if len(self.thoughts) < 2:
            return True, ""

        past_thought = self.thoughts[-1]
        past_tool_name = past_thought.next_tool_name
        past_tool_args = past_thought.next_tool_args

        # Exact match check - definitely reject
        if next_tool_name == past_tool_name and next_tool_args == past_tool_args:
            return False, f"Exact duplicate of attempt {i} steps ago"

        return True, ""

    def add_action(self, action: EnhancedCOT.Action) -> bool: # don't add if thought is repeated
        # if not self.is_valid_tool_call(action.next_tool_name, action.next_tool_args):
        #     return False
        self.thoughts.append(action)
        return True

    def to_str(self):
        messages=[]
        for i,thought in enumerate(self.thoughts):
            if thought.is_deleted:
                continue
            if i<len(self.thoughts)-self.latest_observations_to_keep:
                assistant_str = (
                    f"next_thought:{thought.next_thought}\n"
                    f"next_tool_name:{thought.next_tool_name}\n"
                    f"next_tool_args:{thought.next_tool_args}\n"
                )
                # Compute observation summary length safely for str/list/None
                if thought.observation is None:
                    _obs_len = 0
                elif isinstance(thought.observation, (list, tuple)):
                    _obs_len = len(thought.observation)
                else:
                    _obs_len = len(str(thought.observation).splitlines())
                user_str=( f"observation: {'error ocurred.' if thought.is_error else ''} "
                           f"output omitted ({_obs_len}) lines\n")

            else:
                if thought.is_error is None or i==len(self.thoughts)-1:
                    assistant_str=f"next_thought:{thought.next_thought}\nnext_tool_name:{thought.next_tool_name}\nnext_tool_args:{thought.next_tool_args}"
                    # Render list observations as JSON array for the model
                    if isinstance(thought.observation, (list, tuple)):
                        try:
                            obs_render=json.dumps(list(thought.observation), ensure_ascii=False)
                        except Exception:
                            obs_render=str(thought.observation)
                    else:
                        obs_render=str(thought.observation)
                    user_str=f"observation: {obs_render}"
                else:
                    if self.thoughts[-1].is_error==None and thought.is_error!=None:
                        assistant_str = (
                            f"next_thought:{thought.next_thought}\n"
                            f"next_tool_name:{thought.next_tool_name}\n"
                            f"next_tool_args:{thought.next_tool_args}")
                        if thought.observation is None:
                            _obs_len = 0
                        elif isinstance(thought.observation, (list, tuple)):
                            _obs_len = len(thought.observation)
                        else:
                            _obs_len = len(str(thought.observation).splitlines())
                        user_str=(
                            f"observation: error ocurred. detailed output omitted "
                            f"({_obs_len}) lines\n"
                        )
                    else:
                        assistant_str=f"next_thought:{thought.next_thought}\nnext_tool_name:{thought.next_tool_name}\nnext_tool_args:{thought.next_tool_args}"
                        if isinstance(thought.observation, (list, tuple)):
                            try:
                                obs_render=json.dumps(list(thought.observation), ensure_ascii=False)
                            except Exception:
                                obs_render=str(thought.observation)
                        else:
                            obs_render=str(thought.observation)
                        user_str=f"observation: {obs_render}"
            messages.append({"role":"assistant","content":assistant_str})
            messages.append({"role":"user","content":user_str})
        return messages

    def export_to_csv(self,file_path:str="./xray.csv"):
        with open(file_path, "w") as f:
            writer=csv.writer(f)
            writer.writerow(["next_thought","next_tool_name","next_tool_args","observation","is_error","raw_response","total_attempts","is_deleted"])
            if len(self.thoughts)>0:
                for thought in self.thoughts:
                    writer.writerow([thought.next_thought,thought.next_tool_name,thought.next_tool_args,thought.observation,thought.is_error,thought.raw_response,thought.total_attempts,str(thought.inference_error_counter),str(thought.request_data),len(str(thought.request_data)),thought.is_deleted])


    def get_tokens_used(self):
        # quick, safe heuristic assuming ~0.75 tokens/word
        msgs = self.to_str()
        text = "\n".join(m["content"] for m in msgs)
        word_count = len(text.split())
        return int(word_count * 0.75)

class Utils:
    @classmethod
    def get_available_modules(cls) -> set[str]:
        """Return the set of top-level module names that can be imported in the
        *current* Python environment.

        The result includes:
        • built-in/stdlib module names (`sys.builtin_module_names`)
        • every top-level name discoverable on `sys.path` via `pkgutil.iter_modules()`
        This is useful when we need to check whether a piece of code depends on a
        package that is *not* present in the environment.
        """
        import sys, pkgutil

        available: set[str] = set(sys.builtin_module_names)
        for module_info in pkgutil.iter_modules():
            # Only keep the top-level package name (before the first dot)
            top_level = module_info.name.split(".")[0]
            available.add(top_level)
        return available

    @classmethod
    def message_to_str(cls,messages:list[dict]):
        final_str=""
        for message in messages:
            role=message["role"]
            content=message["content"]
            final_str+=f"{role}: {content}\n"
        return final_str

    @classmethod
    def limit_strings(cls,strings: str, n=1000)->str:
        '''
        Limit the number of strings to 1000
        '''
        strings_list=strings.split("\n")
        if len(strings_list)>n:
            return "\n".join(strings_list[:n])+"\n..." + f"({len(strings_list)-n} more lines)"
        else:
            return strings
    @classmethod
    def load_json(cls,json_string:str)->dict:
        try:
            return json.loads(json_string)
        except Exception as e:
            try:
                return eval(json_string)
            except Exception as e:
                logger.info(f"unable to fix manually, trying with llm")
                fixed_json=EnhancedNetwork.fix_json_string_with_llm(json_string)
                # if fixed_json == ""
                if fixed_json:
                    return fixed_json
                else:
                    raise JSONDecodeError(f"Invalid JSON: {json_string}")
    @classmethod
    def log_to_failed_messages(cls,text_resp:str):
        with open("../failed_messages.csv","a") as f:
            writer=csv.writer(f)
            writer.writerow([text_resp])

class FunctionVisitor(ast.NodeVisitor):
    def __init__(self, file_content: str):
        self.functions = {}
        self.current_class = None
        self.class_hierarchy = []
        self.file_content = file_content

    def visit_ClassDef(self, node):
        self.class_hierarchy.append(node.name)
        self.current_class = "::".join(self.class_hierarchy)
        self.generic_visit(node)
        self.class_hierarchy.pop()
        self.current_class = "::".join(self.class_hierarchy) if self.class_hierarchy else None

    def _process_function(self, node):
        full_function_name = f"{self.current_class}::{node.name}" if self.current_class else node.name
        line_number = node.lineno
        if isinstance(node.decorator_list, list) and len(node.decorator_list) > 0:
            line_number = node.decorator_list[0].lineno

        end_line_number = line_number
        if isinstance(node.body, list) and len(node.body) > 0:
            end_line_number = node.body[-1].lineno

        lines = self.file_content.split("\n")
        body = "\n".join(lines[line_number-1:end_line_number])

        self.functions[full_function_name] = {
            "class": self.current_class,
            "body": body,
            "line_number": line_number
        }
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._process_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._process_function(node)

    def visit_Module(self, node):
        self.current_class = None
        self.generic_visit(node)
        self.current_class = None

class ClassVisitor(ast.NodeVisitor):
    def __init__(self, file_content: str):
        self.classes = {}
        self.file_content = file_content

    def visit_ClassDef(self, node):
        line_number = node.lineno
        if isinstance(node.decorator_list, list) and len(node.decorator_list) > 0:
            line_number = node.decorator_list[0].lineno
        end_line_number = line_number
        if isinstance(node.body, list) and len(node.body) > 0:
            end_line_number = node.body[-1].lineno
        lines = self.file_content.split("\n")
        body = "\n".join(lines[line_number-1:end_line_number])
        self.classes[node.name] = {
            "body": body,
            "line_number": line_number
        }
        self.generic_visit(node)

class EnhancedNetwork:
    class ErrorType(Enum):
        EMPTY_RESPONSE=1
        RESERVED_TOKEN_PRESENT=2
        RATE_LIMIT_EXCEEDED=3
        INVALID_RESPONSE_FORMAT=4
        TIMEOUT=5
        UNKNOWN=6
        NETWORK_ERROR=7
        AUTHENTICATION_ERROR=8
        RESOURCE_EXHAUSTED=9

    @classmethod
    def is_valid_response(cls,raw_text:str)->bool:
        if type(raw_text) is dict and raw_text.get("error",None) is not None and raw_text.get("error")!="":
            return False,cls.ErrorType.EMPTY_RESPONSE.name
        if not raw_text.strip().endswith("}") and not raw_text.strip().endswith("}]"):
            return False, "Incomplete response, your response must be shorter to fit within context limit"
        if len(raw_text)==0:
            return False, cls.ErrorType.EMPTY_RESPONSE.name
        if "<|reserved_token_" in raw_text:
            return False, cls.ErrorType.RESERVED_TOKEN_PRESENT.name
        if 'API request failed with status 429' in raw_text:
            return False, cls.ErrorType.RATE_LIMIT_EXCEEDED.name
        if 'Read timed out' in raw_text:
            return False, cls.ErrorType.TIMEOUT.name
        if 'Network unreachable' in raw_text or 'Connection refused' in raw_text:
            return False, cls.ErrorType.NETWORK_ERROR.name
        return True, None

    @classmethod
    def get_error_counter(cls)->dict[str,int]:
        return {
            k:0 for k in cls.ErrorType.__members__
        }

    @classmethod
    def fix_json_string_with_llm(cls,json_string:str,attempt:int=0)->dict:
        messages=[
            {"role":"system", "content":"Fix the json string sent by the user.  Reply only with the json string and nothing else."},
            {"role":"user", "content":json_string}
        ]
        response=cls.make_request(messages, model=DEEPSEEK_MODEL_NAME)
        try:
            response=response.replace('```json','').strip('```')
            response=json.loads(response)
            return response
        except JSONDecodeError as e:
            logger.error(f"Error fixing json string: {e},trying again..")
            logger.error(f"json string is :{json_string}")
            logger.error(f"LLM response is :{response}")
            return None

    @classmethod
    def make_request(cls,messages:list,model:str,attempt:int=0, temperature:float=0.0)->str:
        global run_id
        url = f"{DEFAULT_PROXY_URL.rstrip('/')}/api/inference"
        print("[REQUEST] run_id:", run_id)

        # Cache miss - make the actual request
        request_data = {
            "run_id": run_id if run_id else "1",
            "messages": messages,
            "temperature": temperature,
        }

        headers = {
            "Content-Type": "application/json"
        }
        request_data['model'] = model
        response = requests.post(url, json=request_data, timeout=120, headers=headers)

        response.raise_for_status()
        response_json = response.json()
        is_oai_interface= type(response_json) is dict and response_json.get('choices') is not None and len(response_json.get('choices'))>0 and response_json.get('choices')[0].get('message') is not None
        if is_oai_interface:
            raw_text=response_json['choices'][0]['message']['content']
        else:
            if type(response_json) is str:
                raw_text=response_json.strip("\n").strip()
            else:
                raw_text=response_json
        if type(raw_text) is not dict:
            raw_text=raw_text.lstrip()
        return raw_text

    @classmethod
    def _request_next_action_with_retry(cls, messages: dict,
                                        model: str,
                                        max_retries: int = 5,
                                        base_delay: float = 1.0,
                                        temperature: float = 0.0) -> str:

        raw_text='not defined'
        error_counter=cls.get_error_counter()
        next_thought, next_tool_name, next_tool_args = None, None, None
        total_attempts=0
        for attempt in range(max_retries):
            try:
                total_attempts+=1
                index = AGENT_MODELS.index(model) if model in AGENT_MODELS else -1
                raw_text=cls.make_request(messages,model=AGENT_MODELS[(index + attempt)%len(AGENT_MODELS)], temperature=temperature)
                is_valid,error_msg=cls.is_valid_response(raw_text)
                if not(is_valid):
                    raise Exception(error_msg)

                next_thought, next_tool_name, next_tool_args,error_msg = cls.parse_response(raw_text)
                if error_msg:
                    raise Exception(error_msg)
                break
            except Exception as e:
                error_body = str(e)
                logger.error(f"Error: {error_body}")
                if attempt < max_retries:
                    delay = base_delay
                    logger.info(error_body)
                    logger.error("--------------------------------")
                    logger.error(f"response: {raw_text}")
                    logger.error("--------------------------------")
                    logger.info(f"[agent] Retrying in {delay} seconds... (attempt {attempt + 1}/{max_retries})")
                    if "RATE_LIMIT_EXCEEDED" in error_body:
                        error_counter[cls.ErrorType.RATE_LIMIT_EXCEEDED.name]+=1
                    elif "RESERVED_TOKEN_PRESENT" in error_body:
                        error_counter[cls.ErrorType.RESERVED_TOKEN_PRESENT.name]+=1
                    elif "EMPTY_RESPONSE" in error_body:
                        error_counter[cls.ErrorType.EMPTY_RESPONSE.name]+=1
                    elif "TIMEOUT" in error_body:
                        error_counter[cls.ErrorType.TIMEOUT.name]+=1
                    elif "Invalid JSON" in error_body:
                        error_counter[cls.ErrorType.INVALID_RESPONSE_FORMAT.name]+=1
                    elif "Invalid response" in error_body:
                        error_counter[cls.ErrorType.INVALID_RESPONSE_FORMAT.name]+=1
                    else:
                        error_counter[cls.ErrorType.UNKNOWN.name]+=1
                    if "RATE_LIMIT_EXCEEDED" not in error_body and "RESERVED_TOKEN_PRESENT" not in error_body and "EMPTY_RESPONSE" not in error_body and  "TIMEOUT" not in error_body:
                        messages.append({"role":"assistant","content":raw_text})
                        messages.append({"role":"user","content":"observation: "+error_body})
                    time.sleep(random.uniform(1.2*delay, 1.5*delay))
                    continue
                else:
                    error_counter[cls.ErrorType.TIMEOUT.name]+=1
                    raise RuntimeError(error_body)

        return next_thought, next_tool_name, next_tool_args,raw_text,total_attempts,error_counter,messages


    @classmethod
    def parse_malformed_json(cls,arguments:list[str], json_string:str)->dict | str:
        # pattern of general json string with unescaped " in values keys from keys list
        pattern = ''
        for i, k in enumerate(arguments):
            pattern += f'"{k}": (.*)'
            if i != len(arguments) - 1:
                pattern += r',\s*'

        match=re.search(pattern, json_string)

        if not match:
            return f"Error: {json_string} can not match pattern {pattern}"

        result_json={}
        for i in range(len(arguments)):
            value=match.group(i+1)
            value=value.strip()
            if value.startswith('"') and value.endswith('"'):
                value=value[1:-1]
            #value=value.replace('"', '\\"')
            value=value.replace('\\n','\n')
            result_json[arguments[i]]=value
        return result_json

    @classmethod
    def parse_next_tool_args(cls,tool_name:str, next_tool_args: str)->dict | str:
        '''
        parse string to json, fix unecaped " in values like this: '{"a": "text "text2" text3 "text4"", "b": "text3"}'
        returns json or error message
        '''

        next_tool_args=next_tool_args.replace('```json','').strip('```')
        error_msg=''

        try:
            next_tool_args = Utils.load_json(next_tool_args.strip())
        except JSONDecodeError as e:
            error_msg=f"Invalid JSON: {next_tool_args}"
            try:
                next_tool_args = cls.parse_malformed_json(EnhancedToolManager.get_tool_args_for_tool(tool_name,required=True), next_tool_args)
            except EnhancedToolManager.Error as e:
                raise Exception(e.message)
            except Exception as e:
                raise Exception(error_msg)
        return next_tool_args

    @classmethod
    def inference(cls, messages: List[Dict[str, Any]], model: str, run_id: str = "1",return_json:bool=False, temperature:float=0.0) -> dict:
        """Prod inference with caching"""
        cleaned_msgs: List[Dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue
            content = m.get("content", "")

            if role == "assistant" and not content.strip():
                continue

            cleaned_msgs.append({"role": role, "content": content})

        if not cleaned_msgs:
            raise RuntimeError("No valid messages to send to proxy.")

        next_thought,next_tool_name,next_tool_args,raw_text,total_attempts,error_counter,messages = cls._request_next_action_with_retry(cleaned_msgs, model=model, temperature=temperature)

        return next_thought,next_tool_name,next_tool_args,raw_text,total_attempts,error_counter,messages

    @classmethod
    def sanitise_text_resp(cls,text_resp:str)->str:
        # remove all leading and trailing quotes
        text_resp=re.sub("[\'\"]*next_thought[\'\"]*:","next_thought:",text_resp)
        text_resp=re.sub("[\'\"]*next_tool_name[\'\"]*:","next_tool_name:",text_resp)
        text_resp=re.sub("[\'\"]*next_tool_args[\'\"]*:","next_tool_args:",text_resp)
        text_resp=re.sub("[\'\"]*observation[\'\"]*:","observation:",text_resp)
        if "next_thought" not in text_resp and "next_tool_name:" in text_resp and "next_tool_args:" in text_resp and text_resp.find("next_tool_name:")<text_resp.find("next_tool_args:") and text_resp.find("next_tool_name:")>10:
            logger.info(f"next_thought not found in {text_resp[:50]}, adding it")
            text_resp="next_thought: "+text_resp
        if "next_tool_name:" in text_resp and "next_tool_args:" in text_resp and text_resp.find("next_tool_name:")<text_resp.find("next_tool_args:"):
            # remove all leading and trailing quotes in tool_name
            next_tool_name=text_resp.split("next_tool_name:")[1].split("next_tool_args:")[0].strip().strip("\n").strip("\'").strip("\"").strip()
            text_resp=re.sub(f"next_tool_name:[\'\" ]*{next_tool_name}[\'\" ]*","next_tool_name: "+next_tool_name,text_resp)

        return text_resp

    @classmethod
    def parse_response(cls,text_resp: str)->tuple[str, Any, Any]:
        error_msg=None
        text_resp = text_resp.strip()
        text_resp=text_resp.split("observation:")[0]
        text_resp=text_resp.strip().strip("\n")
        text_resp=cls.sanitise_text_resp(text_resp)
        if "next_thought:" in text_resp and "next_tool_name:" in text_resp and "next_tool_args:" in text_resp and text_resp.find("next_thought:")<text_resp.find("next_tool_name:") and text_resp.find("next_tool_name:")<text_resp.find("next_tool_args:"):
            next_thought=text_resp.split("next_thought:")[1].split("next_tool_name:")[0].strip().strip("\n")
            next_tool_name_raw=text_resp.split("next_tool_name:")[1].split("next_tool_args:")[0].strip().strip("\n")
            next_tool_args_raw=text_resp.split("next_tool_args:")[1].strip().split("next_thought:")[0].strip().strip("\n")
            try:
                # Enforce arrays per new contract: if single string/object, wrap as arrays
                if next_tool_name_raw.startswith("["):
                    next_tool_name = Utils.load_json(next_tool_name_raw)
                else:
                    next_tool_name = [next_tool_name_raw]
                parsed_args = cls.parse_next_tool_args(next_tool_name, next_tool_args_raw)
                if isinstance(parsed_args, list):
                    next_tool_args = parsed_args
                else:
                    next_tool_args = [parsed_args for _ in next_tool_name]
            except JSONDecodeError as e:
                error_msg=f"Invalid JSON: {str(e)}"
                Utils.log_to_failed_messages(text_resp)

        else:
            if "next_thought:" not in text_resp:
                error_msg="Invalid response. next_thought not found"
            elif "next_tool_name:" not in text_resp:
                error_msg="Invalid response. next_tool_name not found"
            elif "next_tool_args:" not in text_resp:
                error_msg="Invalid response. next_tool_args not found"
            elif text_resp.find("next_thought:")>text_resp.find("next_tool_name:"):
                error_msg="Invalid response. next_thought is after next_tool_name"
            elif text_resp.find("next_tool_name:")>text_resp.find("next_tool_args:"):
                error_msg="Invalid response. next_tool_name is after next_tool_args"
            else:
                logger.error(f"We have no clue why parsing failed. Please check this \n{text_resp}\n")
            Utils.log_to_failed_messages(text_resp)
            return None,None,None,error_msg

        if len(next_tool_name) == 1:
            return next_thought, next_tool_name[0], next_tool_args[0], error_msg

        return next_thought, next_tool_name, next_tool_args,error_msg

class EnhancedToolManager:
    logs = []
    TOOL_LIST = {}

    class Error(Exception):
        class ErrorType(Enum):
            SYNTAX_ERROR=1
            RUNTIME_ERROR=2
            TIMEOUT=3
            FILE_NOT_FOUND=4
            SEARCH_TERM_NOT_FOUND=5
            UNKNOWN=6
            THIRD_PARTY_DEPENDENCIES=7
            MULTIPLE_SEARCH_RESULTS_FOUND=8
            BUG_REPORT_REQUIRED=9
            INVALID_RESPONSE_FORMAT=10
            INVALID_TOOL_NAME=11
            INVALID_FILE_PATH=12
            INVALID_TOOL_CALL=13
            IMPORT_ERROR=14
            GIT_OPERATION_FAILED=15
            GIT_CONFIG_ERROR=16
            GIT_STATE_ERROR=17
            GIT_MERGE_CONFLICT=18
            GIT_BRANCH_ERROR=19
            TEST_COVERAGE_ERROR = 20
            DEPENDENCY_ANALYSIS_ERROR = 21
            CODE_SMELL_DETECTION_ERROR = 22
            GIT_HISTORY_ERROR = 23
            CODE_QUALITY_ERROR = 24
            SOLUTION_VALIDATION_ERROR = 25
            CODE_STYLE_ERROR = 26
            SOLUTION_COMPARISON_ERROR = 27

        def __init__(self,error_type:ErrorType,message:str):
            self.error_type=error_type
            self.message=message

    def tool(fn):
        def wrapper(self, *args, **kwargs):
            self.tool_invocations[fn.__name__]+=1
            try:
                return fn(self, *args, **kwargs)
            except EnhancedToolManager.Error as e:
                self.tool_failure[fn.__name__][e.error_type]+=1
                return e.message

        # Preserve original function metadata

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__signature__ = inspect.signature(fn)
        wrapper.__annotations__ = fn.__annotations__.copy()
        wrapper.is_tool=True

        return wrapper

    def __init__(self, **kwargs):
        pass

    @classmethod
    def tool_parsing(cls,fn):
        tool_schemas = None
        name = fn.__name__
        doc_fn = fn.__doc__ or ""
        # remove parameters section from here to be put in args section
        doc=doc_fn.split("Arguments:")[0]
        output_description=doc_fn.split("Output:")
        if len(output_description)>1:
            output_description="Output: "+output_description[1].strip()
            doc=doc+"\n\n"+output_description
        sig = inspect.signature(fn)
        properties = {}
        required = []
        for param in sig.parameters.values():
            if param.name == 'self':
                continue
            if param.default is param.empty and param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
                required.append(param.name)
            type_hint = str(param.annotation) if param.annotation != param.empty else "string"
            param_description=re.search(f"{param.name}:([^\n]+)",doc_fn)
            if param_description:
                param_description=param_description.group(1)
            else:
                raise ValueError(f"Parameter description not found for {param.name} in {doc_fn}: tool name: {name}")
            # Special handling for list[str] / List[str] annotations so that the
            # generated JSON schema correctly represents an array of strings.
            if ("list" in type_hint.lower()) and ("str" in type_hint):
                properties[param.name] = {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": param_description
                }
                continue
            elif 'str' in type_hint:
                json_type = "string"
            elif 'int' in type_hint:
                json_type = "integer"
            elif 'float' in type_hint:
                json_type = "number"
            elif 'bool' in type_hint:
                json_type = "boolean"
            else:
                json_type = "string"
            properties[param.name] = {
                "type": json_type,
                "description": param_description
            }
        parameters = {
            "type": "object",
            "properties": properties,
            "required": required
        }
        tool_schemas={
            "name": name,
            "description": doc.strip(),
            "input_schema": parameters
        }

        return tool_schemas

    @classmethod
    def get_tool_args_for_tool(self,tool_name:str,required_only:bool=False)->list[str]:
        if tool_name not in self.TOOL_LIST:
            return f"Error: tool '{tool_name}' not found"
        if not required_only:
            return list(self.TOOL_LIST[tool_name]['input_schema']['properties'].keys())
        else:
            return self.TOOL_LIST[tool_name]['input_schema']['required']

    def get_tool_docs(self)->str:
        return '\n\n'.join([json.dumps(tool_metadata, ensure_ascii=False) for _,tool_metadata in self.TOOL_LIST.items()])

    def get_tool(self,tool_name:str):
        if tool_name not in self.TOOL_LIST:
            return f"Error: tool '{tool_name}' not found"
        tool_method = getattr(self, tool_name, None)
        if tool_method is None or not callable(tool_method):
            return f"Error: tool '{tool_name}' does not exist. Please use one of the following tools: {', '.join(self.TOOL_LIST.keys())}"

        return tool_method

    def _add_line_numbers_to_content(self, content: str, start_line: int = 1) -> str:
        """Helper method to add line numbers to content."""
        lines = content.splitlines()
        numbered_lines = []
        for i, line in enumerate(lines):
            line_num = start_line + i
            numbered_lines.append(f"{line_num:6}|{line}")
        return '\n'.join(numbered_lines)

    def _add_context_to_similar_match(self, original_content: str, formatted_match: str, context_lines: int = 2) -> str:
        """Add context lines around a similar match for better understanding."""
        lines = original_content.split('\n')

        # Extract the actual content from the formatted match (remove the description part)
        match_lines = formatted_match.split('\n')
        if len(match_lines) < 2:
            return formatted_match

        # Skip the description line (e.g., "Lines 45-47: ..." or "Line 23: ...")
        actual_content_lines = match_lines[1:]
        actual_content = '\n'.join(actual_content_lines)

        # Find where this content appears in the original file
        best_match_start = -1
        best_similarity = 0

        # Search for the best matching position in the original content
        for i in range(len(lines) - len(actual_content_lines) + 1):
            candidate_lines = lines[i:i + len(actual_content_lines)]
            candidate_content = '\n'.join(candidate_lines)

            import difflib
            similarity = difflib.SequenceMatcher(None, actual_content.strip(), candidate_content.strip()).ratio()
            if similarity > best_similarity:
                best_similarity = similarity
                best_match_start = i

        if best_match_start == -1:
            return formatted_match  # Fallback to original if can't find position

        # Calculate context boundaries
        start_line = max(0, best_match_start - context_lines)
        end_line = min(len(lines), best_match_start + len(actual_content_lines) + context_lines)

        # Build the context with line numbers
        context_lines_list = []
        for i in range(start_line, end_line):
            line_num = i + 1
            prefix = ">>> " if best_match_start <= i < best_match_start + len(actual_content_lines) else "    "
            context_lines_list.append(f"{prefix}{line_num:4}| {lines[i]}")

        # Extract original description
        description = match_lines[0] if match_lines else f"Match found at lines {best_match_start+1}-{best_match_start+len(actual_content_lines)}"

        return f"{description}\n" + "\n".join(context_lines_list)

    def _find_most_similar_content(self, original_content: str, search_string: str, max_results: int = 3) -> list[tuple[float, str]]:
        """Find the most similar content chunks to the search string."""
        import difflib

        # Split content into meaningful chunks
        lines = original_content.split('\n')

        # Try different chunk sizes to find the best match
        chunks = []

        # Individual lines
        for i, line in enumerate(lines):
            if line.strip():  # Skip empty lines
                chunks.append((f"Line {i+1}: {line.strip()}", line.strip()))

        # Multi-line chunks (3-5 lines) for better context
        search_lines = search_string.split('\n')
        target_chunk_size = max(3, len(search_lines))

        for i in range(len(lines) - target_chunk_size + 1):
            chunk_lines = lines[i:i + target_chunk_size]
            chunk_content = '\n'.join(chunk_lines).strip()
            if chunk_content:
                chunks.append((f"Lines {i+1}-{i+target_chunk_size}: ...", chunk_content))

        # Calculate similarity scores
        similarities = []
        for chunk_desc, chunk_content in chunks:
            ratio = difflib.SequenceMatcher(None, search_string.strip(), chunk_content).ratio()
            if ratio > 0.3:  # Only include reasonably similar content
                similarities.append((ratio, chunk_desc, chunk_content))

        # Sort by similarity and return top results
        similarities.sort(key=lambda x: x[0], reverse=True)
        return [(ratio, f"{desc}\n{content}") for ratio, desc, content in similarities[:max_results]]

    def get_final_git_patch(self) -> str:
        '''
        Generates git diff patch containing all modifications in working directory
        Useful for capturing comprehensive change summary before finalization
        '''
        try:
            # Update to include cfg, txt, and toml files along with py files
            command = """
            shopt -s globstar

            cp .gitignore .gitignore.backup 2>/dev/null || true
            echo 'src/agent.py' >> .gitignore
            echo 'src/agent_runner.py' >> .gitignore

            git add **/*.py 2>/dev/null || true
            git add **/*.toml 2>/dev/null || true
            git add **/*.cfg 2>/dev/null || true
            git add **/*.txt 2>/dev/null || true

            git diff --cached > .patch.txt
            cat .patch.txt

            mv .gitignore.backup .gitignore 2>/dev/null || true
            """
            print("Generating git patch...")
            output = subprocess.run(["bash", "-c", command], timeout=30, capture_output=True, text=True)

            # output = output.stdout.decode("utf-8") + '\n' + output.stderr.decode("utf-8")
            return output.stdout
        except Exception as e:
            logger.error(f"Error generating git patch: {e}")
            return f"Error generating git patch: {e}"

    def _analyze_code_for_type_mismatches(self, content: str, file_path: str) -> list:
        """
        Analyze code for potential type mismatches before execution.
        Returns a list of warnings about potential issues.
        """
        import ast

        warnings = []

        try:
            tree = ast.parse(content)

            # Track constants defined with range()
            constants_from_range = {}

            # Look for patterns where constants are defined with range()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    # Check if RHS is range()
                    if isinstance(node.value, ast.Call):
                        if hasattr(node.value.func, 'id') and node.value.func.id == 'range':
                            # Multiple assignment with range
                            if isinstance(node.targets[0], ast.Tuple):
                                for i, elt in enumerate(node.targets[0].elts):
                                    if isinstance(elt, ast.Name):
                                        constants_from_range[elt.id] = i

            # Also check main.py if this is a test file
            if 'test' in file_path.lower() and os.path.exists('main.py'):
                try:
                    with open('main.py', 'r') as f:
                        main_tree = ast.parse(f.read())

                    # Look for constants in main.py too
                    for node in ast.walk(main_tree):
                        if isinstance(node, ast.Assign):
                            if isinstance(node.value, ast.Call):
                                if hasattr(node.value.func, 'id') and node.value.func.id == 'range':
                                    if isinstance(node.targets[0], ast.Tuple):
                                        for i, elt in enumerate(node.targets[0].elts):
                                            if isinstance(elt, ast.Name):
                                                constants_from_range[elt.id] = i
                except:
                    pass

            # Now check for string literal comparisons with these constants
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    # Check for comparisons with string literals that match constants
                    for comparator in node.comparators:
                        if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                            # Check if this string matches a known constant
                            if comparator.value in constants_from_range:
                                warnings.append(
                                    f"WARNING: Comparing with string '{comparator.value}' but {comparator.value} "
                                    f"is defined as integer constant (value={constants_from_range[comparator.value]}). "
                                    f"Use {comparator.value} instead of '{comparator.value}'"
                                )

                # Check for tuples/lists containing string literals that should be constants
                if isinstance(node, (ast.Tuple, ast.List)):
                    for elt in node.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            if elt.value in constants_from_range:
                                warnings.append(
                                    f"WARNING: Using string literal '{elt.value}' in collection, but {elt.value} "
                                    f"is defined as integer constant (value={constants_from_range[elt.value]}). "
                                    f"Use {elt.value} instead of '{elt.value}'"
                                )

                # Check function calls with string arguments that should be constants
                if isinstance(node, ast.Call):
                    for arg in node.args:
                        # Check for tuples passed as arguments
                        if isinstance(arg, ast.Tuple):
                            if len(arg.elts) > 0 and isinstance(arg.elts[0], ast.Constant):
                                if isinstance(arg.elts[0].value, str) and arg.elts[0].value in constants_from_range:
                                    warnings.append(
                                        f"WARNING: Passing tuple with string '{arg.elts[0].value}' as first element, "
                                        f"but {arg.elts[0].value} is an integer constant. "
                                        f"Use ({arg.elts[0].value}, ...) instead of ('{arg.elts[0].value}', ...)"
                                    )

            # Check imports - if importing from main, ensure constants are imported
            if 'test' in file_path.lower():
                imports_from_main = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if node.module and 'main' in node.module:
                            for alias in node.names:
                                imports_from_main.append(alias.name)

                # Check if constants are being used without import
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name):
                        if node.id in constants_from_range and node.id not in imports_from_main:
                            warnings.append(
                                f"WARNING: Using {node.id} but it's not imported. "
                                f"Add 'from main import {node.id}' or it will be treated as undefined."
                            )

        except Exception as e:
            # Don't fail on analysis errors, just skip
            pass

        return warnings

class CreateTaskEnhancedToolManager(EnhancedToolManager):

    def __init__(self, available_tools: Optional[list[str]] = None, test_cases: list[dict] = [], test_file: str = "custom_tests"):

        self.TOOL_LIST={}
        self.test_file = test_file
        if "." not in self.test_file:
            self.test_file = self.test_file + ".py"

        self.test_cases = test_cases
        self.can_finish = False
        self.checkpoint = ""
        self.should_checkpoint = False
        self.blacklisted_test_files = []

        # Check all classes in the method resolution order (MRO) to include inherited tools
        for cls in self.__class__.__mro__:
            for name, attr in cls.__dict__.items():
                if getattr(attr, "is_tool", False) and name not in self.TOOL_LIST:
                    if available_tools is not None and name not in available_tools: # if available_tools is provided, only include tools in the list
                        continue
                    self.TOOL_LIST[name] = self.__class__.tool_parsing(attr)

        self.tool_failure={
            k:{j:0 for j in self.Error.ErrorType.__members__} for k in self.TOOL_LIST.keys()
        }

        self.tool_invocations={
            k:0 for k in self.TOOL_LIST.keys()
        }

    def _is_test_file(self, file_path: str) -> bool:
        return "test" in file_path.lower() or "spec." in file_path.lower()

    def _run_test(self) -> tuple[str, bool]:
        """
        Executes the test file using Python and returns the result and pass/fail status.
        """
        try:
            # Run the test file with Python
            result = subprocess.run(
                ["python", self.test_file],
                capture_output=True,
                text=True,
                timeout=60
            )
            output = result.stdout + result.stderr
            # Heuristic: if exit code is 0, consider test passed
            is_passed = result.returncode == 0
            return output, is_passed
        except Exception as e:
            return f"Error running test file '{self.test_file}': {e}", False

    def _run_unitests(self, test_cases: list[str] = None) -> tuple[str, bool]:
        """
        Executes the test file using Python and returns the result and pass/fail status.
        """
        import io
        import contextlib

        try:
            # Capture print output
            output_capture = io.StringIO()

            with contextlib.redirect_stdout(output_capture), contextlib.redirect_stderr(output_capture):
                # Clear module cache to ensure we load the latest version of files
                import sys
                import ast

                # Parse test file to find all imported modules
                modules_to_clear = set()
                try:
                    with open(self.test_file, 'r') as f:
                        tree = ast.parse(f.read())

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                modules_to_clear.add(alias.name.split('.')[0])
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                modules_to_clear.add(node.module.split('.')[0])
                except Exception:
                    pass

                if "unittest" in modules_to_clear:
                    modules_to_clear.discard("unittest")

                # Remove modules from sys.modules
                for module_name in list(sys.modules.keys()):
                    for clear_name in modules_to_clear:
                        if module_name == clear_name or module_name.startswith(clear_name + '.'):
                            del sys.modules[module_name]
                            break

                # Load tests module
                test_file_path = os.getcwd() + "/" + self.test_file
                with open(test_file_path, 'r') as f:
                    test_code = f.read()

                # Create a new module namespace
                tests_module = type(sys)('tests')
                tests_module.__file__ = test_file_path

                # Execute the code in the module's namespace
                exec(compile(test_code, test_file_path, 'exec'), tests_module.__dict__)

                # Find test class (should inherit from unittest.TestCase)
                test_class = None
                for name in dir(tests_module):
                    obj = getattr(tests_module, name)
                    if (isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj is not unittest.TestCase):
                        test_class = obj
                        break

                if not test_class:
                    captured_output = output_capture.getvalue()
                    return f"No test class found in tests.py\nCaptured output:\n{captured_output}", False

                # Get test methods
                test_methods = [method for method in dir(test_class) if method.startswith("test_")]

                test_results = []
                test_instance = test_class()

                # Run tests with progress tracking and detailed error output
                for method_name in test_methods:
                    if test_cases and method_name not in test_cases: # only run specified test cases
                        continue
                    try:
                        method = getattr(test_instance, method_name)
                        method()
                        test_results.append(f"{method_name}: PASSED")
                    except Exception as e:
                        error_output = traceback.format_exc()
                        captured_output = output_capture.getvalue()

                        # Build the failure message with captured output
                        failure_message = f"{method_name}: FAILED - {e}\n"
                        if captured_output.strip():
                            failure_message += f"Captured output:\n{captured_output}\n"
                        failure_message += error_output

                        # Add helpful hint if this looks like a string comparison mismatch
                        error_str = str(e)
                        if "!=" in error_str and isinstance(e, AssertionError):
                            # Check if this is comparing two strings (both sides have quotes)
                            parts = error_str.split("!=")
                            if len(parts) == 2:
                                left = parts[0].strip()
                                right = parts[1].strip()
                                # If both sides are quoted strings, this is likely a message format issue
                                if ((left.startswith("'") or left.startswith('"')) and
                                    (right.startswith("'") or right.startswith('"'))):

                                    # Extract the actual string content (remove quotes)
                                    left_content = left.strip("'\"")
                                    right_content = right.strip("'\"")

                                    # Check if one string has UPPERCASE words that appear as Titlecase in the other
                                    import re
                                    left_words = set(re.findall(r'\b[A-Z]{2,}\b', left_content))
                                    right_words = set(re.findall(r'\b[A-Z][a-z]+\b', right_content))

                                    # Check if any uppercase word from left appears as titlecase in right
                                    casing_mismatch = False
                                    for upper_word in left_words:
                                        title_word = upper_word.capitalize()
                                        if title_word in right_words or title_word in right_content:
                                            casing_mismatch = True
                                            break

                                    # Also check the reverse
                                    if not casing_mismatch:
                                        right_upper_words = set(re.findall(r'\b[A-Z]{2,}\b', right_content))
                                        left_title_words = set(re.findall(r'\b[A-Z][a-z]+\b', left_content))
                                        for upper_word in right_upper_words:
                                            title_word = upper_word.capitalize()
                                            if title_word in left_title_words or title_word in left_content:
                                                casing_mismatch = True
                                                break

                                    failure_message += "\nHINT: The test expects a different string format. "
                                    if casing_mismatch:
                                        failure_message += "The strings differ in capitalization - one uses UPPERCASE words while "
                                        failure_message += "the other uses Titlecase. You need to update your error message in the "
                                        failure_message += "implementation code to match the exact format expected by the test. "
                                        failure_message += f"Change your code from: {left_content} to: {right_content}\n"
                                    else:
                                        failure_message += "Check if the expected string uses different capitalization, "
                                        failure_message += "word order, or phrasing than what your code produces. "
                                        failure_message += "Update your implementation code to match the expected format.\n"

                        test_results.append(failure_message)
                        return "\n".join(test_results), False

            # Get captured output
            captured_output = output_capture.getvalue()
            final_output = ""
            if captured_output.strip():
                final_output += f"Captured output:\n{captured_output}\n"
            final_output += "\n".join(test_results)

            return final_output, True

        except Exception as e:
            return f"Error running test file '{self.test_file}': {e}", False

    def _get_file_content(self, file_path: str, add_line_numbers: bool = False) -> str:
        with open(file_path, "r") as f:
            content = f.read()

        if add_line_numbers:
            content = self._add_line_numbers_to_content(content)

        return content

    def _check_syntax_error(self, content: str) -> tuple[bool, str]:
        # Simple syntax error check for Python code
        try:
            ast.parse(content)
            return False, ""
        except SyntaxError as e:
            return True, f"Syntax error: {e}"

    def _save_file(self, file_path: str, content: str) -> None:
        with open(file_path, "w") as f:
            f.write(content)

    @EnhancedToolManager.tool
    def test_file_generation_finish(self) -> str:
        '''
        Signals completion of the test file generation workflow execution
        Arguments:
            None
        Output:
            Success message or error message
        '''
        return "finish"

    def _analyze_code_for_constants(self, file_path: str = 'main.py') -> dict:
        """Analyze main file to detect constants and required imports"""
        if not os.path.exists(file_path):
            return {'constants': [], 'imports_needed': [], 'warnings': []}

        try:
            with open(file_path, 'r') as f:
                content = f.read()
            result = {
                'constants': [],
                'imports_needed': [],
                'warnings': []
            }

            # Check for constant definitions using range()
            import re
            constant_pattern = r'^([A-Z_]+(?:,\s*[A-Z_]+)*)\s*=\s*range\(\d+\)'
            for match in re.finditer(constant_pattern, content, re.MULTILINE):
                constants = [c.strip() for c in match.group(1).split(',')]
                result['constants'].extend(constants)
                result['imports_needed'].extend(constants)
                result['warnings'].append(
                    f"Constants {', '.join(constants)} are defined as integers. "
                    f"Use them directly (e.g., {constants[0]}) not as strings ('{constants[0]}'). "
                    f"Note: Error messages may use different capitalization or formatting than the constant names."
                )

            # Check for individual constant definitions
            single_constant_pattern = r'^([A-Z_]+)\s*=\s*(\d+|"[^"]+"|\'[^\']+\')'
            for match in re.finditer(single_constant_pattern, content, re.MULTILINE):
                const_name = match.group(1)
                if const_name not in result['constants']:
                    result['constants'].append(const_name)
                    result['imports_needed'].append(const_name)

            return result
        except Exception as e:
            return {'constants': [], 'imports_needed': [], 'warnings': [str(e)]}

    def _extract_error_messages_from_tests(self, test_content: str) -> list:
        """Extract expected error messages from test file assertions"""
        import re
        error_messages = []

        # Pattern to find assertEqual assertions with exception messages
        # Examples:
        # - assertEqual(str(context.exception), "Edge is malformed")
        # - assertEqual(err.exception.args[0], "Edge is malformed")
        patterns = [
            r'assertEqual\(str\(context\.exception\),\s*["\']([^"\']+)["\']\)',
            r'assertEqual\((?:\w+)\.exception\.args\[0\],\s*["\']([^"\']+)["\']\)',
            r'assertEqual\(str\((?:\w+)\.exception\),\s*["\']([^"\']+)["\']\)'
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, test_content):
                error_msg = match.group(1)
                if error_msg not in error_messages:
                    error_messages.append(error_msg)

        return error_messages

    def _fix_test_content_for_constants(self, test_content: str, constants: list) -> str:
        """Fix test content to use actual constants instead of string literals"""
        fixed_content = test_content
        for const in constants:
            # Replace string literals with actual constants in tuples and comparisons
            fixed_content = re.sub(
                rf"\(\s*['\"]({const})['\"]",
                rf"({const}",
                fixed_content
            )
            # Also fix comparisons
            fixed_content = re.sub(
                rf"==\s*['\"]({const})['\"]",
                rf"== {const}",
                fixed_content
            )

        return fixed_content


    @EnhancedToolManager.tool
    def create_test_file(self, whole_content: str) -> str:
        '''
        Creates a test file and writes the provided content to it.
        Arguments:
            whole_content: the content to write to the file
        Output:
            Success message or error message
        '''
        if os.path.exists(self.test_file):
            return f"Error: file '{self.test_file}' already exists."

        # Analyze main.py for constants and patterns
        analysis = self._analyze_code_for_constants('main.py')

        # Fix test content if constants were found
        if analysis['constants']:
            original_content = whole_content
            whole_content = self._fix_test_content_for_constants(whole_content, analysis['constants'])

            # Add proper imports if missing
            if analysis['imports_needed']:
                import_line = f"from main import {', '.join(analysis['imports_needed'])}"
                if import_line not in whole_content:
                    if 'from main import' in whole_content:
                        # Update existing import
                        whole_content = re.sub(
                            r'from main import ([^\n]+)',
                            lambda m: f"from main import {m.group(1)}, {', '.join(analysis['imports_needed'])}"
                                      if not any(const in m.group(1) for const in analysis['imports_needed'])
                                      else m.group(0),
                            whole_content
                        )
                    else:
                        # Add new import after existing imports or at top
                        import_pos = whole_content.find('\n\n')
                        if import_pos == -1:
                            import_pos = 0
                        whole_content = whole_content[:import_pos] + f"\n{import_line}" + whole_content[import_pos:]

            # Add warning comment if content was modified
            if original_content != whole_content:
                warning_comment = (
                    "# NOTE: Constants like " + ', '.join(analysis['constants'][:3]) +
                    " are integers, not strings!\n"
                )
                if warning_comment not in whole_content:
                    whole_content = warning_comment + whole_content

        is_error, error = self._check_syntax_error(whole_content)
        if is_error:
            return f"Error: syntax error in file '{self.test_file}': {error}"
        try:
            with open(self.test_file, "w") as f:
                f.write(whole_content)

            # Extract and list specific error messages from the test file
            error_messages = self._extract_error_messages_from_tests(whole_content)

            # Return success with any warnings
            msg = f"File '{self.test_file}' created successfully."
            if analysis.get('warnings'):
                msg += "\n\nIMPORTANT: " + "\n".join(analysis['warnings'])

            # Add specific error message list if found
            if error_messages:
                msg += f"\n\nCRITICAL - EXACT ERROR MESSAGES REQUIRED IN YOUR CODE:\nThis test file expects these EXACT error message strings (character-by-character):\n"
                for i, err_msg in enumerate(error_messages, 1):
                    msg += f"{i}. \"{err_msg}\"\n"
                msg += "\nWhen implementing error handling, use EXACTLY these strings (same capitalization, punctuation, and word order)."
            else:
                msg += "\n\nCRITICAL: Before implementing error handling, READ THIS TEST FILE to see the EXACT error message strings expected in assertions. Error messages in your implementation MUST match these character-by-character."

            return msg
        except Exception as e:
            return f"Error: failed to create file '{self.test_file}': {e}"

    @EnhancedToolManager.tool
    def get_file_content(self, file_path: str) -> str:
        '''
        Returns the content of the file.
        Arguments:
            file_path: path to the file
        Output:
            content of the file
        '''
        if not os.path.exists(file_path):
            return f"Error: file '{file_path}' does not exist."
        try:
            with open(file_path, 'r') as f:
                content = f.read()

            # If reading test file, extract and remind about exact error messages
            if 'test' in os.path.basename(file_path).lower():
                error_messages = self._extract_error_messages_from_tests(content)
                if error_messages:
                    reminder = "\n\n" + "="*60 + "\nCRITICAL REMINDER - EXACT ERROR MESSAGE STRINGS REQUIRED:\n"
                    for i, err_msg in enumerate(error_messages, 1):
                        reminder += f"{i}. \"{err_msg}\"\n"
                    reminder += "\nWhen implementing error handling, you MUST use EXACTLY these strings.\n"
                    reminder += "Same capitalization, punctuation, and word order required.\n"
                    reminder += "="*60
                    content += reminder

            return content
        except Exception as e:
            return f"Error reading file: {e}"

    @EnhancedToolManager.tool
    def apply_code_edit(self,file_path:str, search:str, replace:str)->str:
        '''
        Performs targeted text replacement within source files.
        Your search and replace string should be **as minimal as** possible.
        Arguments:
            file_path: target file for modification
            search: exact text pattern to locate and replace
            replace: new text content to substitute

        Output:
            operation status - success confirmation or detailed error with guidance
        '''
        if search == replace:
            return "ERROR: search and replace are the same. Please provide a different search and replace."
        if not os.path.exists(file_path):
            return f"Error: file '{file_path}' does not exist."

        original=self._get_file_content(file_path)

        match original.count(search):
            case 0:
                # Find most similar content to help LLM correct the search string
                similar_matches = self._find_most_similar_content(original, search, 1)

                error_msg = f"Error: search string not found in file {file_path}."

                if similar_matches:
                    error_msg += f"\n\nMost similar snippet found (you may need to adjust your search string):"
                    for i, (ratio, content) in enumerate(similar_matches, 1):
                        similarity_pct = int(ratio * 100)
                        # Add context lines around the match for better understanding
                        content_with_context = self._add_context_to_similar_match(original, content, context_lines=2)
                        error_msg += f"\n\n{i}. Similarity: {similarity_pct}%\n{content_with_context}"
                else:
                    error_msg += " No similar content found. Please check the file content and provide the exact code you want to replace."

                return error_msg
            case 1:

                new_content = original.replace(search, replace)
                try:
                    is_error,error=self._check_syntax_error(new_content)

                    if not is_error:
                        # Run type mismatch analysis on the new content
                        warnings = self._analyze_code_for_type_mismatches(new_content, file_path)

                        self._save_file(file_path, new_content)

                        if warnings:
                            warning_msg = "\n".join(warnings)
                            return f"ok, code edit applied successfully\n\n{warning_msg}"
                        return "ok, code edit applied successfully"
                    else:
                        return f"Error: code edit failed. {error}"
                except Exception as e:
                    return f"Error: syntax error in file {file_path}. {e}"
            case num_hits:
                return f"Error: search string found {num_hits} times in file '{file_path}'.\nPlease reformulate your search and replace to apply only one change."

    @EnhancedToolManager.tool
    def run_test(self, test_cases: list[str] = None) -> str:
        '''
        Verify the solution by running the test cases.
        Arguments:
            test_cases: test cases to run. If you want to run whole test, test_case should be None, otherwise, test cases joined by ", "(e.g. ["add_1", "add_2"])
        Output:
            result of the test cases
        '''
        return self._run_unitests(test_cases)[0]

    @EnhancedToolManager.tool
    def finish(self):
        '''
        Finish the workflow with verification and if everything's working as expected, return "finish".
        Only use this tool when you've completed implementing a solution including all the edge cases.
        Arguments:
            None
        Output:
            "finish" if the solution is working as expected, otherwise "Test failed, you should try again\n\n" + result_str
        '''
        # Check if any files have been modified or created by generating the git patch
        patch = self.get_final_git_patch()

        # Check if the patch is empty or just contains error messages
        if not patch or len(patch.strip()) == 0 or patch.startswith("Error generating git patch"):
            raise EnhancedToolManager.Error(
                EnhancedToolManager.Error.ErrorType.INVALID_TOOL_CALL.name,
                "Error: You cannot finish yet. No files have been created or modified. "
                "You've only proposed a solution but haven't implemented it. "
                "Please use save_file to implement your solution first. "
                "Remember: You need to actually write the code, not just describe it."
            )

        # Check if the patch only contains test files
        patch_lines = patch.split('\n')
        modified_files = []
        for line in patch_lines:
            if line.startswith('diff --git'):
                # Extract filename from diff line like: diff --git a/path/file.py b/path/file.py
                parts = line.split()
                if len(parts) >= 3:
                    filename = parts[2].lstrip('a/')
                    modified_files.append(filename)

        # Check if all modified files are test files
        if modified_files:
            non_test_files = [f for f in modified_files if not ('test' in f.lower() or f.startswith('test_') or f.endswith('_test.py'))]
            if not non_test_files:
                raise EnhancedToolManager.Error(
                    EnhancedToolManager.Error.ErrorType.INVALID_TOOL_CALL.name,
                    "Error: You cannot finish yet. You've only created or modified test files. "
                    "You need to implement the actual solution code, not just tests. "
                    "Please use save_file to implement your solution in the main code files."
                )

        result_str, is_test_passed = self._run_test()

        if is_test_passed:
            self.checkpoint = patch  # Use the patch we already generated
            return "finish"
        else:
            return "Test failed, you should try again\n\n" + result_str

class FixTaskEnhancedToolManager(EnhancedToolManager):

    def __init__(self, available_tools: Optional[list[str]] = None):
        self.new_files_created=[]
        self.TOOL_LIST = {}

        # Check all classes in the method resolution order (MRO) to include inherited tools
        for cls in self.__class__.__mro__:
            for name, attr in cls.__dict__.items():
                if getattr(attr, "is_tool", False) and name not in self.TOOL_LIST:
                    if available_tools is not None and name not in available_tools: # if available_tools is provided, only include tools in the list
                        continue
                    self.TOOL_LIST[name] = self.__class__.tool_parsing(attr)

        self.tool_failure={
            k:{j:0 for j in self.Error.ErrorType.__members__} for k in self.TOOL_LIST.keys()
        }

        self.tool_invocations={
            k:0 for k in self.TOOL_LIST.keys()
        }

    def check_syntax_error(self,content:str,file_path:str="<unknown>")->bool:
        try:
            ast.parse(content, filename=file_path)
            return False, None
        except SyntaxError as e:
            logger.error(f"Syntax error: {e}")
            return True, EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.SYNTAX_ERROR.name,f"Syntax error. {str(e)}")

    def _get_file_content(self,file_path: str, search_start_line: int = None, search_end_line: int = None, search_term: str = None,limit:int=5000)->str:
        if search_term is not None and search_term!="":
            logger.debug(f"search_term specified: {search_term}, searching in v2")
            return self.search_in_specified_file_v2(file_path, search_term)

        # check if start and end line are not between a function..
        func_ranges=self.get_function_ranges(file_path)
        if search_start_line!=None:
            for start, end, name in func_ranges:
                if start<=search_start_line<=end:
                    if start<search_start_line:
                        logger.debug(f"search start line {search_start_line} is between a function {start}-{end} for function {name}, setting to {start}")
                        search_start_line=start
        if search_end_line!=None:
            for start, end, name in func_ranges:
                if start<=search_end_line<=end:
                    if end>search_end_line:
                        logger.debug(f"search end line {search_end_line} is between a function {start}-{end} for function {name}, setting to {end}")
                        search_end_line=end
        logger.debug(f"search start line: {search_start_line}, search end line: {search_end_line}")
        with open(file_path, "r") as f:
            if search_start_line is not None or search_end_line is not None:
                lines = f.readlines()
                start = max(0, (search_start_line or 1) - 1)  # Convert to 0-based
                end = min(len(lines), search_end_line or len(lines))
                content = ''.join(lines[start:end])
                return f"Lines {start+1}-{end} of {file_path}:\n{content}"
            else:
                content = f.read()

        return Utils.limit_strings(content, n=limit) if limit!=-1  else content

    @EnhancedToolManager.tool
    def get_file_content(self,file_path: str, search_start_line: int = None, search_end_line: int = None, search_term: str = None)->str:

        '''
        Retrieves file contents with optional filtering based on search term and line numbers
        Arguments:
            file_path: filesystem path to target file. This file must be python file.
            search_start_line: optional start line number to begin extraction (1-indexed)
            search_end_line: optional end line number to end extraction (1-indexed)
            search_term: optional text pattern to filter matching lines
        '''
        return self._get_file_content(file_path,search_start_line,search_end_line,search_term,limit=5000)

    @EnhancedToolManager.tool
    def save_file(self,file_path: str, content: str)->str:
        '''
        Writes text content to specified filesystem location. If there are any syntax errors in the code, it rejects the edit with an error message. Do not use this tool to create test or files to reproduce the error.
        Arguments:
            file_path: target filesystem path
            content: text data to write
        '''
        if "test" in file_path.lower() or "reproduce" in file_path.lower():
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.INVALID_TOOL_CALL.name,f"Error: You cannot use this tool to create test or files to reproduce the error.")
        return self._save(file_path, content)

    def _save(self,file_path: str, content: str)->str:
        is_syntax_error, error = self.check_syntax_error(content)
        if not is_syntax_error:
            with open(file_path, "w") as file:
                file.write(content)
            self.new_files_created.append(file_path)
            return f"File {file_path} saved successfully"
        else:
            logger.error(f"Error saving file: {error.message}")
            error.message="Error saving file. "+error.message
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.SYNTAX_ERROR.name,error.message)

    @EnhancedToolManager.tool
    def get_functions(function_paths: List[str]) -> Dict[str, str]:
        '''
        Get functions from a list of function paths.
        Arguments:
            function_paths: list of function paths (e.g. ["folder1/file1.py::class1::function1", "folder2/file2.py::class2::function2"])
        Output:
            dictionary of functions with function paths as keys and function bodies as values
        '''
        functions = {}
        for function_path in function_paths:
            parts = function_path.split("::")
            file_path = parts[0]
            function_name = "::".join(parts[1:])
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = ast.parse(content, filename=file_path)
                visitor = FunctionVisitor(content)
                visitor.visit(tree)

                if function_name in visitor.functions:
                    functions[function_path] = visitor.functions[function_name].get("body", "")
                else:
                    functions[function_path] = f"Function {function_name} not found in {file_path}"
            except FileNotFoundError:
                functions[function_path] = f"File {file_path} not found"
            except Exception as e:
                functions[function_path] = f"Error processing {file_path}: {str(e)}"

        return functions

    @EnhancedToolManager.tool
    def get_classes(class_paths: List[str])->Dict[str, str]:
        '''
        Get classes from a list of class paths.
        Arguments:
            class_paths: list of class paths (e.g. ["folder1/file1.py::class1", "folder2/file2.py::class2"])
        Output:
            dictionary of classes with class paths as keys and class bodies as values
        '''
        classes = {}
        for class_path in class_paths:
            parts = class_path.split("::")
            file_path = parts[0]
            class_name = "::".join(parts[1:])
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = ast.parse(content, filename=file_path)
                visitor = ClassVisitor(content)
                visitor.visit(tree)
                if class_name in visitor.classes:
                    classes[class_path] = visitor.classes[class_name].get("body", "")
                else:
                    classes[class_path] = f"Class {class_name} not found in {file_path}"
            except FileNotFoundError:
                classes[class_path] = f"File {file_path} not found"
            except Exception as e:
                classes[class_path] = f"Error processing {file_path}: {str(e)}"

        return classes

    @EnhancedToolManager.tool
    def search_in_all_files_content(self,search_term: str)->str:
        '''
        Search for a text pattern across all .py files in the project, excluding any file with "test" in its path.
        Use at the beginning of the workflow to locate all possible references to a function, class, or variable.
        If more context is needed (e.g., surrounding functions, classes, etc.), follow up with get_classes or get_functions.

        Arguments:
            search_term: text pattern to locate (e.g., "def test_function", "*SomeClass*")
        Output:
            locations where pattern was found with file paths and line numbers
        '''
        output=[]

        # Walk through all directories and find Python files
        for root, _, files in os.walk("."):
            # Skip .git and docs directories
            if ".git" in root or "docs" in root:
                continue

            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    # Skip test files
                    # if 'test' in file_path.lower():
                    #     continue

                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()

                        if not re.search(search_term, content, re.IGNORECASE):
                            continue

                        # Parse the file content using AST
                        tree = ast.parse(content, filename=file_path)
                        visitor = FunctionVisitor(content)
                        visitor.visit(tree)

                        for function_name, function_info in visitor.functions.items():
                            body = function_info["body"]
                            if re.search(search_term, body, re.IGNORECASE):
                                lines = body.split("\n")
                                for idx, line in enumerate(lines):
                                    if re.search(search_term, line, re.IGNORECASE):
                                        line_number = function_info["line_number"] + idx
                                        output.append(f"{file_path}:{line_number} | {function_name} | {line.rstrip()}")
                    except Exception as e:
                        logger.error(f"Error searching in file {file_path} with search term {search_term}: {e}")

        output=Utils.limit_strings("\n".join(output), n=100)
        if not output:
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.SEARCH_TERM_NOT_FOUND.name,f"'{search_term}' not found in the codebase.")
        return output

    def get_function_ranges(self,file_path: str)->list[tuple[int, int, str]]:
        # Try to parse the file to map lines to their enclosing functions.
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_lines = f.read().splitlines()
        except Exception as e:
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.FILE_NOT_FOUND.name,f"Error reading '{file_path}': {e}")
        try:
            tree = ast.parse("\n".join(source_lines), filename=file_path)
        except SyntaxError as e:
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.SYNTAX_ERROR.name,f"Error parsing '{file_path}': {e}, {traceback.format_exc()}")
            tree = None  # Fallback if file cannot be parsed.

        func_ranges: list[tuple[int, int, str]] = []  # (start, end, name)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = getattr(node, 'lineno', None)
                    end = getattr(node, 'end_lineno', None)
                    if start is not None and end is not None:
                        func_ranges.append((start, end, node.name))
        return func_ranges

    def _extract_function_matches(self,file_path: str, search_term: str, *, max_output_lines: int = 1000) -> str:
        '''
        Return the source code of any function definitions that contain `search_term`.
        If a match occurs outside of a function, only that line is returned. The final
        output is truncated with `limit_strings` to avoid excessive verbosity.
        '''
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_lines = f.read().splitlines()
        except Exception as e:
            logger.error(f"Error reading '{file_path}': {e}")
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.FILE_NOT_FOUND.name,f"Error reading '{file_path}': {e}")

        # Identify all lines that contain the search term.
        match_lines = [idx + 1 for idx, line in enumerate(source_lines) if search_term in line]
        if not match_lines:
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.SEARCH_TERM_NOT_FOUND.name,f"'{search_term}' not found in file '{file_path}'")

        func_ranges=self.get_function_ranges(file_path)

        def _containing_function(line_no: int):
            for start, end, name in func_ranges:
                if start <= line_no <= end:
                    return (start, end, name)
            return None

        functions_to_return: list[tuple[int, int, str]] = []
        standalone_lines: list[int] = []
        for ln in match_lines:
            info = _containing_function(ln)
            if info and info not in functions_to_return:
                functions_to_return.append(info)
            elif not info:
                standalone_lines.append(ln)

        chunks: list[str] = []
        for start, end, name in functions_to_return:
            func_src = "\n".join(source_lines[start - 1:end])
            chunks.append(f"(lines {start}-{end}):\n{func_src}")

        for ln in standalone_lines:
            chunks.append(f"{ln}:{source_lines[ln - 1]}")

        return Utils.limit_strings("\n\n".join(chunks), n=max_output_lines)

    @EnhancedToolManager.tool
    def search_in_specified_file_v2(self,file_path: str, search_term: str)->str:
        '''
        Locates text patterns within a specific file
        Arguments:
            file_path: target file for pattern matching. This file must be python file.
            search_term: text pattern to find (e.g., "def test_function", "*SomeClass*")
        Output:
            matching locations with line numbers, or error description
        '''
        if not file_path.endswith(".py"):
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.INVALID_FILE_PATH.name,f"Error: file '{file_path}' is not a python file.")
        return self._extract_function_matches(file_path, search_term)

    @EnhancedToolManager.tool
    def start_over(self,problem_with_old_approach:str,new_apprach_to_try:str):
        '''
        This will revert any changes made to the codebase and let's you start over. Only use this tool when you have concluded that current changes you made to the codebase are not relevant and you want to start again with new approach.
        Arguments:
            problem_with_old_approach: What you tried and what was the key issues you faced with this approach.
            new_apprach_to_try: What is the new approach you want to try and how it will fix the issues you faced earlier.
        '''
        logger.info("============Start Over============")
        os.system("git reset --hard")
        logger.info(f"problem_with_old_approach: {problem_with_old_approach}")
        logger.info(f"new_apprach_to_try: {new_apprach_to_try}")
        logger.info("===========================")
        return "Done, codebase reverted to initial state. You can start over with new approach."

    def get_final_git_patch(self) -> str:
        """
        Generate a clean unified diff (staged changes only) that tools like `patch`
        or `git apply` can consume.
        """
        try:
            # Stage modified/untracked files with desired extensions, excluding agent files.
            exts = (".py", ".ini", ".cfg", ".toml")
            exclude = {"src/agent.py", "src/agent_runner.py"}

            # Discover modified + untracked files
            ls = subprocess.run(
                ["git", "ls-files", "-m", "-o", "--exclude-standard"],
                capture_output=True, text=True, timeout=30, check=True
            ).stdout.splitlines()

            to_add = [f for f in ls if f.endswith(exts) and f not in exclude]
            if to_add:
                subprocess.run(["git", "add", "--"] + to_add, check=True, timeout=30)

            # Produce a clean, parseable patch (no colors; standard unified diff).
            diff = subprocess.run(
                ["git", "diff", "--cached", "--no-color", "--unified=3"],
                capture_output=True, text=True, timeout=30, check=True
            )

            # Log stderr separately so it never pollutes the patch.
            if diff.stderr:
                logger.warning("git diff (stderr): %s", diff.stderr.strip())

            patch_text = diff.stdout or ""
            return patch_text
        except Exception as e:
            logger.exception("Error generating git patch")
            return f"Error generating git patch: {e}"

    def create_new_file(self,file_path:str, content:str)->str:
        '''
        Generates new file with specified content at target location. Do not use this tool to create test or files to reproduce the error unless user has specifically asked you to create test files as part of problem statement.
        Arguments:
            file_path: destination path for new file
            content: text content for file creation
        '''
        if "test" in file_path.lower() or "reproduce" in file_path.lower():
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.INVALID_TOOL_CALL.name,f"Error: You cannot use this tool to create test or files to reproduce the error.")
        return self._save(file_path, content)

    @EnhancedToolManager.tool
    def run_code(self,content:str,file_path:str)->str:
        '''
        Runs any python code. You can use this tool directly to run any test code or bug reproduction code.
        Saves the code at the given file_path and then runs it. Do not use this tool to create test or files to reproduce the error unless user has specifically asked you to create test files as part of problem statement.

        Arguments:
            content: text code to write in file
            file_path: path of the file to save the code in. This file should always be in the current working directory.

        Output:
            Returns the stdout/stderr from the executed file.
            Returns error message if there are any third party dependencies.
        '''
        # Run pre-execution analysis
        warnings = self._analyze_code_for_type_mismatches(content, file_path)

        self._save(file_path, content)

        # Parse the file's AST to collect import statements

        with open(file_path, "r") as f:
            tree = ast.parse(f.read(), filename=file_path)

        disallowed_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Use the module specified in 'from x import y' if available;
                # otherwise fall back to the imported name from plain 'import x'
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module.split(".")[0]
                else:
                    mod = node.names[0].name.split(".")[0]

                # Skip if built-in module
                if mod in sys.builtin_module_names:
                    continue



                # Skip relative imports ("from . import foo") which have level > 0
                if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
                    continue

                # --- Additional check: allow local modules/packages in CWD ---
                cwd = os.getcwd()
                local_file = os.path.join(cwd, f"{mod}.py")
                local_pkg_init = os.path.join(cwd, mod, "__init__.py")
                local_pkg_dir = os.path.join(cwd, mod)
                # Also check inside a conventional 'lib' folder within cwd
                lib_dir = os.path.join(cwd, 'lib')
                lib_file = os.path.join(lib_dir, f"{mod}.py")
                lib_pkg_init = os.path.join(lib_dir, mod, "__init__.py")
                lib_pkg_dir = os.path.join(lib_dir, mod)

                if (
                    os.path.isfile(local_file)
                    or os.path.isfile(local_pkg_init)
                    or os.path.isdir(local_pkg_dir)
                    or os.path.isfile(lib_file)
                    or os.path.isfile(lib_pkg_init)
                    or os.path.isdir(lib_pkg_dir)
                ):
                    # Treat as local dependency, allow it
                    continue

                # Any other module is considered disallowed
                disallowed_modules.add(mod)

        if disallowed_modules and False:
            logger.error(f"Cannot run, third party dependencies detected: {sorted(disallowed_modules)}\n")
            raise ToolManager.Error(ToolManager.Error.ErrorType.THIRD_PARTY_DEPENDENCIES.name,f"Error:Cannot run, third party dependencies detected: {sorted(disallowed_modules)}\n")


        result = subprocess.run(["python", file_path], capture_output=True, text=True, check=False, timeout=60)
        if result.returncode!=0:

            error_type=EnhancedToolManager.Error.ErrorType.RUNTIME_ERROR
            if "ImportError" in result.stderr:
                error_type=EnhancedToolManager.Error.ErrorType.IMPORT_ERROR
            if "ModuleNotFoundError" in result.stderr:
                error_type=EnhancedToolManager.Error.ErrorType.THIRD_PARTY_DEPENDENCIES
            raise EnhancedToolManager.Error(error_type,f"Error running code: {result.stderr}\n")

        observation = f"{result.stdout}\n"

        # Add warnings to observation if any were found
        if warnings:
            observation += "\n" + "\n".join(warnings)

        return observation

    @EnhancedToolManager.tool
    def apply_code_edit(self,file_path:str, search:str, replace:str)->str:
        '''
        Performs targeted text replacement within source files. If there are any syntax errors in the code, it rejects the edit with an error message.
        Arguments:
        file_path: target file for modification
        search: exact text pattern to locate and replace
        replace: new text content to substitute

        Output:
            operation status - success confirmation or detailed error with guidance
        '''
        if not os.path.exists(file_path):
            logger.error(f"file '{file_path}' does not exist.")
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.FILE_NOT_FOUND.name,f"Error: file '{file_path}' does not exist.")

        original=self._get_file_content(file_path,limit=-1)

        match original.count(search):
            case 0:
                logger.error(f"search string not found in file {file_path}. You need to share the exact code you want to replace.")
                raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.SEARCH_TERM_NOT_FOUND.name,f"Error: search string not found in file {file_path}. You need to share the exact code you want to replace.")
            case 1:

                new_content = original.replace(search, replace)

                # Check for potential type mismatch fixes that go the wrong way
                import re
                edit_warnings = []

                # Check if changing from constant comparison to string comparison
                const_match = re.search(r'\b(\w+)\s*==\s*([A-Z_]+)\b', search)
                if const_match:
                    var_name = const_match.group(1)
                    const_name = const_match.group(2)
                    # Check if the replacement has the same constant as a string literal
                    string_pattern = rf'\b{re.escape(var_name)}\s*==\s*[\'\"]({re.escape(const_name)})[\'"]'
                    if re.search(string_pattern, replace):
                        warning = f"WARNING: You're changing {const_name} (likely a constant) to '{const_name}' (a string). This may be incorrect if {const_name} is defined as an integer constant."
                        logger.warning(warning)
                        edit_warnings.append(warning)

                try:
                    is_error,error=self.check_syntax_error(new_content)
                    if not is_error:

                        # Run type mismatch analysis on the new content
                        warnings = self._analyze_code_for_type_mismatches(new_content, file_path)

                        # Combine edit warnings with analysis warnings
                        all_warnings = edit_warnings + warnings

                        self.save_file(file_path, new_content)

                        if all_warnings:
                            warning_msg = "\n".join(all_warnings)
                            return f"ok, code edit applied successfully\n\n{warning_msg}"
                        return "ok, code edit applied successfully"
                    else:
                        error.message="code edit failed. "+error.message
                        raise error
                except EnhancedToolManager.Error as e:
                    raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.SYNTAX_ERROR.name,f"Error: syntax error in file {file_path}. {e.message}")
            case num_hits:
                logger.error(f"search string found {num_hits} times in file '{file_path}'.\nPlease reformulate your search and replace to apply only one change.")
                raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.MULTIPLE_SEARCH_RESULTS_FOUND.name,f"Error: search string found {num_hits} times in file '{file_path}'.\nPlease reformulate your search and replace to apply only one change.")

    @EnhancedToolManager.tool
    def finish(self,investigation_summary: str):
        '''
        Signals completion of the current workflow execution
        Arguments:
            investigation_summary: Please provide a detailed summary of the findings from your investigation and detailed solution to the problem.Use the following format:
                Problem: <problem_statement>
                Investigation: <investigation_summary>
                Solution: <your solution>
        '''

        # Check if any files have been modified or created by generating the git patch
        patch = self.get_final_git_patch()

        # Check if the patch is empty or just contains error messages
        if not patch or len(patch.strip()) == 0 or patch.startswith("Error generating git patch"):
            # Also check our tracked list as a fallback
            if not self.new_files_created:
                raise EnhancedToolManager.Error(
                    EnhancedToolManager.Error.ErrorType.INVALID_TOOL_CALL.name,
                    "Error: You cannot finish yet. No files have been created or modified. "
                    "You've only proposed a solution but haven't implemented it. "
                    "Please use apply_code_edit or save_file to implement your solution first. "
                    "Remember: You need to actually write the code, not just describe it."
                )

        # Check if the patch only contains test files
        patch_lines = patch.split('\n')
        modified_files = []
        for line in patch_lines:
            if line.startswith('diff --git'):
                # Extract filename from diff line like: diff --git a/path/file.py b/path/file.py
                parts = line.split()
                if len(parts) >= 3:
                    filename = parts[2].lstrip('a/')
                    modified_files.append(filename)

        # Check if all modified files are test files
        if modified_files:
            non_test_files = [f for f in modified_files if not ('test' in f.lower() or f.startswith('test_') or f.endswith('_test.py'))]
            if not non_test_files:
                raise EnhancedToolManager.Error(
                    EnhancedToolManager.Error.ErrorType.INVALID_TOOL_CALL.name,
                    "Error: You cannot finish yet. You've only created or modified test files. "
                    "You need to implement the actual solution code, not just tests. "
                    "Please use apply_code_edit or save_file to implement your solution in the main code files."
                )

        qa_response={"is_patch_correct":"yes"}
        if qa_response.get("is_patch_correct","no").lower()=="yes":
            return "finish"
        else:
            raise EnhancedToolManager.Error(EnhancedToolManager.Error.ErrorType.BUG_REPORT_REQUIRED.name,qa_response.get("analysis",""))

def ensure_git_initialized():
    """Initialize git repository if not already initialized, with temporary config."""
    print("[DEBUG] Starting git initialization check...")

    work_dir = os.getcwd()
    original_cwd = os.getcwd()

    try:
        print(f"[DEBUG] Work directory: {work_dir}")
        print(f"[DEBUG] Before chdir - pwd shows: {subprocess.run(['pwd'], capture_output=True, text=True).stdout.strip()}")

        os.chdir(work_dir)
        print(f"[DEBUG] After chdir - pwd shows: {subprocess.run(['pwd'], capture_output=True, text=True).stdout.strip()}")

        # Initialize git repo if not already initialized
        if not os.path.exists(".git"):
            print("[DEBUG] Initializing git repository...")
            subprocess.run(["git", "init"], check=True)
            subprocess.run(["git", "config", "--global", "--add", "safe.directory", work_dir])

            # Verify .git was created in current directory
            print(f"[DEBUG] .git exists: {os.path.exists('.git')}")
            print(f"[DEBUG] Files in current dir: {os.listdir('.')[:10]}")  # Show first 10 files

            # Set local git config (only for this repo)
            print("[DEBUG] Setting git config...")
            subprocess.run(["git", "config", "--global", "user.email", "agent@sandbox.local"], check=True)
            subprocess.run(["git", "config", "--global", "user.name", "sandbox_agent"], check=True)

            # Add all files
            print("[DEBUG] Adding all files...")
            subprocess.run(["git", "add", "."], check=True)

            # Commit (ignore error if nothing to commit)
            print("[DEBUG] Creating initial commit...")
            result = subprocess.run(["git", "commit", "-m", "Initial commit"], check=False, capture_output=True, text=True)
            if result.returncode == 0:
                print("[DEBUG] Initial commit created successfully")
            else:
                print(f"[DEBUG] Commit result: {result.stderr.strip()}")

            print("[DEBUG] Git initialization completed successfully")
        else:
            print("[DEBUG] Git repository already exists")
            subprocess.run(["git", "config", "--global", "--add", "safe.directory", work_dir])

    except Exception as e:
        print(f"[DEBUG] ERROR: Could not initialize git repository: {e}")
    finally:
        os.chdir(original_cwd)

def set_env_for_agent():

    if os.getcwd() not in os.environ.get("PYTHONPATH",""):
        os.environ["PYTHONPATH"]=os.environ.get("PYTHONPATH","")+":"+os.getcwd()
    if Path(os.getcwd()+"/lib").exists() and os.getcwd()+"/lib" not in os.environ.get("PYTHONPATH",""):
        os.environ["PYTHONPATH"]=os.environ["PYTHONPATH"]+":"+os.getcwd()+"/lib"

def agent_main(input_dict: Dict[str, Any], repo_dir: str = "repo", test_mode: bool = False):
    """Legacy interface wrapper for backwards compatibility."""
    global DEFAULT_PROXY_URL, REPO_DIR, DEFAULT_TIMEOUT, MAX_TEST_PATCH_TIMEOUT, run_id
    run_id = os.getenv("RUN_ID", "")
    repo_dir = os.path.abspath(repo_dir)
    REPO_DIR = repo_dir
    if test_mode:
        DEFAULT_TIMEOUT = 1000
        MAX_TEST_PATCH_TIMEOUT = 400

    sys.path.insert(0, repo_dir)


    if os.path.exists(repo_dir):
        os.chdir(repo_dir)

    ensure_git_initialized()

    set_env_for_agent()

    try:
        problem_type = check_problem_type(input_dict.get("problem_statement"))

        if problem_type == PROBLEM_TYPE_FIX:
            result = process_fix_task(input_dict)
        else:
            result = process_create_task(input_dict)
    except Exception as e:
        result = process_fix_task(input_dict)

    os.system("git reset --hard")

    return result

def execute_agent_workflow(
    problem_statement: str,
    *,
    timeout: int,
    run_id_1: str,
    instance_id: str = "",
    tool_manager: EnhancedToolManager,
    system_prompt: str,
    instance_prompt: str,
    max_steps: int,
    latest_observations_to_keep: int,
    finish_tool_name: str,
    warning_time_limit: int = 200,
    start_over_time: int = 1000,
    log_prefix: str,
    extra_logs: List[str] = None,
    models: List[str] = [GLM_MODEL_NAME],
    upgrade_model_time: int = 1000 # after this time, upgrade the model to the better one
) -> tuple[Any, List[str], List[Dict[str, Any]], bool]:
    global run_id
    run_id = run_id_1
    cot = EnhancedCOT(latest_observations_to_keep=latest_observations_to_keep)

    logger.info(f"[{log_prefix}] Starting agent execution...")
    logger.info(f"[{log_prefix}] system_prompt: {system_prompt}")
    logger.info(f"[{log_prefix}] instance_prompt: {instance_prompt}")

    start_time = time.time()
    logs: List[str] = []

    # Add any extra logs at the start
    if extra_logs:
        tool_manager.logs.extend(extra_logs)

    logger.info(f"Starting workflow execution with {max_steps} max steps: timeout: {timeout} seconds : run_id: {run_id}")

    model_level = 0
    current_model = models[model_level]
    last_model_upgrade_time = time.time()
    last_start_over_time = time.time()
    start_over = False
    last_try_summarization = None
    consecutive_rejections = 0  # Track consecutive tool call rejections
    temperature = 0.0

    for step in range(max_steps):
        logger.info(f"[{log_prefix}] Execution step {step + 1}/{max_steps}, Elapsed time: {time.time() - start_time} seconds, timeout: {timeout} seconds")
        tool_manager.logs.append(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{log_prefix}] Execution step {step + 1}/{max_steps}, Elapsed time: {time.time() - start_time} seconds, timeout: {timeout} seconds\n\n")
        model_upgrade = False
        start_over = False

        if time.time() - start_time > timeout:
            tool_manager.checkpoint = tool_manager.get_final_git_patch()
            break

        if time.time() - last_model_upgrade_time > upgrade_model_time: # upgrade the model after this time
            if model_level < len(models) - 1:
                model_level = model_level + 1
                current_model = models[model_level]
                # cot.thoughts = []
                logger.info(f"[{log_prefix}] Upgrading model to {current_model}, start over new workflow.")
                tool_manager.logs.append(f"[{log_prefix}] Upgrading model to {current_model}, start over new workflow.\n\n")
                last_model_upgrade_time = time.time()
                model_upgrade = True
            else:
                logger.info(f"[{log_prefix}] No more models to upgrade")

        if time.time() - last_start_over_time > start_over_time:
            last_start_over_time = time.time()
            start_over = True

        if last_try_summarization:
            messages.append({"role": "user", "content": f"AS A REMINDER, Here's what I've tried last time that you shouldn't repeat:\n\n{last_try_summarization}\n\nDO NOT REPEAT THIS APPROACH AGAIN and FIND DIFFERENT PLACE TO CHANGE IN CODEBASE."})


        if start_over:
            logger.info(f"[{log_prefix}] Start over time reached, start over new workflow.")
            tool_manager.logs.append(f"[{log_prefix}] Start over time reached, start over new workflow.\n\n")
            messages.append({"role": "user", "content": "Summarize what you've tried until now using `summarize_what_you_tried` tool. DO NOT USE ANY OTHER TOOLS."})
            next_thought, next_tool_name, next_tool_args, raw_text, total_attempts, error_counter, messages = EnhancedNetwork.inference(messages, model=current_model, run_id=run_id)
            summarization_tool_name = "summarize_what_you_tried"
            if isinstance(next_tool_name, str) and next_tool_name == summarization_tool_name or (isinstance(next_tool_name, list) and summarization_tool_name in next_tool_name):
                if isinstance(next_tool_name, list):
                    next_tool_args = next_tool_args[next_tool_name.index(summarization_tool_name)]

                logger.info(f"[{log_prefix}] Summarizing what you've tried until now and starting over: {next_tool_args['summarization']}")
                tool_manager.logs.append(f"[{log_prefix}] Summarizing what you've tried until now and starting over: {next_tool_args['summarization']}\n\n")
                last_try_summarization = next_tool_args['summarization']
                tool_manager.revert_to_last_checkpoint()
                cot.thoughts = []
                continue
            else:
                logger.info(f"[{log_prefix}] summarization tool call failed: {next_tool_name} called instead of summarize_what_you_tried")
                tool_manager.logs.append(f"[{log_prefix}] summarization tool call failed: {next_tool_name} called instead of summarize_what_you_tried\n\n")
                messages[-1] = {"role": "user", "content": "Please start over the process again using `start_over` tool since you're stuck."}


        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": instance_prompt},
        ]

        messages.extend(cot.to_str())
        messages.append({"role": "system", "content": STOP_INSTRUCTION})

        if time.time() - start_time > timeout - warning_time_limit:
            messages.append({"role": "user", "content": f"YOU'RE RUNNING OUT OF TIME, PLEASE FINISH THE WHOLE PROCESS IN {timeout - time.time() + start_time} SECONDS."})

        try:
            inference_start_time = time.time()
            next_thought, next_tool_name, next_tool_args, raw_text, total_attempts, error_counter, messages = EnhancedNetwork.inference(messages, model=current_model, run_id=run_id, temperature=temperature)
            if temperature > 0.0:
                temperature = 0.0

            logger.info(f"[{log_prefix}] next_thought: {next_thought}\nnext_tool_name: {next_tool_name}\nnext_tool_args: {next_tool_args}\nmodel: {current_model}\nmodel inference time: {time.time() - inference_start_time} seconds")
            tool_manager.logs.append(f"[{log_prefix}] next_thought: {next_thought}\n\nnext_tool_name: {next_tool_name}\n\nnext_tool_args: {next_tool_args}\n\nmodel: {current_model}\n\nmodel inference time: {time.time() - inference_start_time} seconds\n\n")

            if next_thought == None or next_tool_name == None or next_tool_args == None:
                cot.thoughts = cot.thoughts[:-1] # remove last thought
                continue
                # raise Exception("next_thought is None or next_tool_name is None or next_tool_args is None")

            is_valid, rejection_reason = cot.is_valid_tool_call(next_tool_name, next_tool_args)
            if not is_valid:
                consecutive_rejections += 1

                logger.error(f"[{log_prefix}] Duplicate detected: {rejection_reason}. Consecutive rejections: {consecutive_rejections}\n\n")
                tool_manager.logs.append(f"[{log_prefix}] Duplicate detected: {rejection_reason}. Consecutive rejections: {consecutive_rejections}\n\n")

                # Add detailed feedback to the LLM about the rejected tool call
                rejection_feedback = REJECTION_FEEDBACK_PROMPT.format(
                    next_tool_name=next_tool_name,
                    next_tool_args=next_tool_args,
                    consecutive_rejections=consecutive_rejections
                )

                # Add specific reason for rejection
                rejection_feedback += f"\n\nSpecific issue: {rejection_reason}"

                cot.add_action(EnhancedCOT.Action(
                    next_thought=next_thought,
                    next_tool_name=next_tool_name,
                    next_tool_args=next_tool_args,
                    observation=rejection_feedback,
                    is_error=False,
                    raw_response=raw_text,
                    total_attempts=total_attempts,
                    inference_error_counter=error_counter,
                    request_data=messages
                ))
                temperature = 0.7
                continue
            else:
                # Reset consecutive rejections counter on successful tool call
                consecutive_rejections = 0

        except Exception as e:
            import traceback
            error_msg = f"\n\nERROR: {repr(e)} {traceback.format_exc()}"
            tool_manager.logs.append(f"[{log_prefix}] Inference error: {error_msg}\n\n")
            logger.error(f"[{log_prefix}] Inference error: {error_msg}")
            cot.add_action(EnhancedCOT.Action(
                next_thought=f"{error_msg}\n\nPlease try other tools or different arguments.",
                next_tool_name="",
                next_tool_args={},
                observation="",
                is_error=True,
                raw_response=raw_text,
                total_attempts=total_attempts,
                inference_error_counter=error_counter,
                request_data=messages
            ))
            temperature = 0.7
            continue

        try:
            # Support multiple tools per step
            tool_execution_start_time = time.time()
            if isinstance(next_tool_name, list):
                tool_names = [str(n).replace('"','').replace("'","") for n in next_tool_name]
                if isinstance(next_tool_args, list):
                    tool_args_list = next_tool_args
                elif isinstance(next_tool_args, dict) or next_tool_args is None:
                    tool_args_list = [next_tool_args for _ in tool_names]
                else:
                    raise TypeError("Invalid next_tool_args type for multiple tools")
                # Normalize args length and content
                tool_args_list = [({} if (a is None or not isinstance(a, dict)) else a) for a in tool_args_list]
                if len(tool_args_list) < len(tool_names):
                    tool_args_list.extend({} for _ in range(len(tool_names) - len(tool_args_list)))
                elif len(tool_args_list) > len(tool_names):
                    tool_args_list = tool_args_list[:len(tool_names)]
                observations = [None] * len(tool_names)
                def _run(idx:int, name:str, args:dict):
                    tool = tool_manager.get_tool(name)
                    return tool(**args) if args else tool()

                # Run tools sequentially instead of in parallel
                for i, tn in enumerate(tool_names):
                    observations[i] = _run(i, tn, tool_args_list[i])
                next_observation = observations
            else:
                if isinstance(next_tool_name, str) and ('"' in next_tool_name or "'" in next_tool_name):
                    next_tool_name=next_tool_name.replace('"','')
                    next_tool_name=next_tool_name.replace("'","")
                next_observation = tool_manager.get_tool(next_tool_name)(**next_tool_args) if next_tool_args else tool_manager.get_tool(next_tool_name)()
            # Extract the formatting logic to avoid duplication
            formatted_observation = '\n\n'.join(next_observation) if isinstance(next_observation, list) else str(next_observation)
            log_message = f"[{log_prefix}] tool execution time: {time.time() - tool_execution_start_time} seconds\n\nnext_observation:\n\n{formatted_observation}"

            tool_manager.logs.append(log_message)
            logger.info(log_message)

            cot.add_action(EnhancedCOT.Action(
                next_thought=next_thought,
                next_tool_name=next_tool_name,
                next_tool_args=next_tool_args,
                observation=next_observation,
                is_error=False,
                raw_response=raw_text,
                total_attempts=total_attempts,
                inference_error_counter=error_counter,
                request_data=messages
            ))
        except EnhancedToolManager.Error as e:
            import traceback
            error_msg = f"observation: {e.message}"
            tool_manager.logs.append(f"[{log_prefix}] Tool error: {error_msg}\n\n")
            logger.error(f"[{log_prefix}] Tool error: {error_msg}")
            cot.add_action(EnhancedCOT.Action(
                next_thought=next_thought,
                next_tool_name=next_tool_name,
                next_tool_args=next_tool_args,
                observation=error_msg,
                is_error=True,
                raw_response=raw_text,
                total_attempts=total_attempts,
                inference_error_counter=error_counter,
                request_data=messages
            ))
            temperature = 0.7
            continue
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            if isinstance(e, TypeError):
                error_msg = f"observation: {str(e)}"
            else:
                error_msg = f"observation: {repr(e)} {error_traceback}"
            tool_manager.logs.append(f"[{log_prefix}] Tool error: {error_msg}\n\n")
            logger.error(f"[{log_prefix}] Tool error: {error_msg}")
            cot.add_action(EnhancedCOT.Action(
                next_thought=next_thought,
                next_tool_name=next_tool_name,
                next_tool_args=next_tool_args,
                observation=error_msg,
                is_error=True,
                raw_response=raw_text,
                total_attempts=total_attempts,
                inference_error_counter=error_counter,
                request_data=messages
            ))
            temperature = 0.7
            continue

        # Check for finish condition
        if (isinstance(next_tool_name, str) and next_tool_name == finish_tool_name) or (isinstance(next_tool_name, list) and finish_tool_name in next_tool_name):
            if isinstance(next_tool_name, list):
                next_observation = next_observation[next_tool_name.index(finish_tool_name)]
                next_tool_args = next_tool_args[next_tool_name.index(finish_tool_name)]
            if (finish_tool_name == "finish" or finish_tool_name == "test_file_generation_finish") and next_observation == "finish":
                    tool_manager.checkpoint = tool_manager.get_final_git_patch()
                    tool_manager.logs.append(f"[{log_prefix}] Final Patch 2: {tool_manager.checkpoint}")
                    return tool_manager.checkpoint, tool_manager.logs, messages, True  # For finish tool, we'll handle the patch generation in the caller

        tool_manager.logs.append(f"[{log_prefix}] Completed step {step + 1}, continuing to next step\n\n")
        logger.info(f"[{log_prefix}] [CRITICAL] Completed step {step + 1}, continuing to next step")
    logger.info(f"[{log_prefix}] [CRITICAL] Workflow completed after reaching MAX_STEPS ({max_steps})")
    tool_manager.logs.append(f"[{log_prefix}] Workflow completed after reaching MAX_STEPS ({max_steps})\n\n")

    return tool_manager.checkpoint, tool_manager.logs, cot.to_str(), False

def check_problem_type(problem_statement: str) -> str:
    retry = 0
    while retry < 10:
        try:
            messages = [
                {"role": "system", "content": PROBLEM_TYPE_CHECK_PROMPT},
                {"role": "user", "content": f"{problem_statement}\n# Project Tree Structure: \n{get_directory_tree()}"}
            ]

            response = EnhancedNetwork.make_request(messages, model=QWEN_MODEL_NAME)

            if response not in [PROBLEM_TYPE_CREATE, PROBLEM_TYPE_FIX]:
                retry += 1
            else:
                break
        except Exception as e:
            logger.error(f"Error: {e}")
            retry += 1

        time.sleep(2)

    return response


def post_process_instruction(instruction: str) -> str:
    """
    Post-processes instruction to mark whitespaces and empty lines explicitly.
    """
    import re

    def apply_markup(text_block: str) -> str:
        lines = text_block.split('\n')
        processed_lines = []

        for i, line in enumerate(lines):
            if line.strip() == '':
                processed_line = '[EMPTY_LINE]'
            else:
                # Mark trailing and leading spaces
                leading_spaces = len(line) - len(line.lstrip(' '))
                trailing_spaces = len(line) - len(line.rstrip(' '))

                processed_line = line
                if leading_spaces > 0:
                    processed_line = f'[{leading_spaces}_LEADING_SPACES]' + line.lstrip(' ')
                if trailing_spaces > 0:
                    processed_line = processed_line.rstrip(' ') + f'[{trailing_spaces}_TRAILING_SPACES]'

            processed_lines.append(f"\"{processed_line}\"")

        return "[\n    " + ",\n    ".join(processed_lines) + "\n]"

    # Pattern to match ```text...``` blocks
    pattern = r'```text\n(.*?)\n```'

    def replace_text_block(match):
        text_content = match.group(1)
        processed_content = apply_markup(text_content)

        return f'```text\n{processed_content}\n```'

    # Replace all text blocks with processed versions
    processed_instruction = re.sub(pattern, replace_text_block, instruction, flags=re.DOTALL)
    return processed_instruction

def process_create_task(input_dict):
    problem_statement = input_dict.get("problem_statement", "")
    problem_statement = post_process_instruction(problem_statement)
    print(problem_statement)

    start_time = time.time()
    test_cases = test_case_extraction(problem_statement)
    print(f"test_cases: {test_cases}")
    elapsed_time = time.time() - start_time

    test_file = test_code_generation(problem_statement=problem_statement, test_cases=test_cases)
    timeout = DEFAULT_TIMEOUT - elapsed_time


    result = create_task_solve_workflow(
        problem_statement,
        test_cases,
        test_file,
        timeout=timeout
    )

    return result

def get_code_skeleton() -> str:
    # Get Python file extensions
    extensions = PYTHON_EXTENSIONS

    # Initialize the result string
    result = ""

    # Walk through the current directory
    for root, _, files in os.walk("."):
        for file in files:
            # Check if the file has a proper extension
            if any(file.endswith(ext) for ext in extensions):
                file_path = os.path.join(root, file)
                with open(file_path, "r") as f:
                    content = f.read()
                # Concatenate the file name and content
                result += f"{file}\n{{\n{content}\n}}\n\n"

    return result

def get_directory_tree(start_path: str = '.') -> str:
    """
    Returns a tree structure representation of the current working directory,
    excluding directories that start with a dot.

    Args:
        start_path: The starting directory path (defaults to current directory)

    Returns:
        A string representation of the directory tree structure
    """
    tree_lines = []

    def add_directory_tree(path: str, prefix: str = "", is_last: bool = True, is_root: bool = False):
        """Recursively build the tree structure"""
        try:
            # Get the directory name
            dir_name = os.path.basename(path) if path != '.' else os.path.basename(os.getcwd())

            # Add current directory to tree (skip for root directory)
            if not is_root:
                connector = "└── " if is_last else "├── "
                tree_lines.append(f"{prefix}{connector}{dir_name}/")

            # Get all items in directory
            try:
                items = os.listdir(path)
                # Filter out hidden directories and files starting with '.'
                items = [item for item in items if not item.startswith('.')]
                items.sort()

                # Separate directories and files
                dirs = []
                files = []
                for item in items:
                    item_path = os.path.join(path, item)
                    if os.path.isdir(item_path):
                        dirs.append(item)
                    else:
                        files.append(item)

                # Process directories first
                for i, dir_name in enumerate(dirs):
                    dir_path = os.path.join(path, dir_name)
                    is_last_dir = (i == len(dirs) - 1) and len(files) == 0
                    new_prefix = prefix + ("" if is_root else ("    " if is_last else "│   "))
                    add_directory_tree(dir_path, new_prefix, is_last_dir, False)

                # Then process files
                for i, file_name in enumerate(files):
                    is_last_file = i == len(files) - 1
                    connector = "└── " if is_last_file else "├── "
                    tree_lines.append(f"{prefix}{'' if is_root else ('    ' if is_last else '│   ')}{connector}{file_name}")

            except PermissionError:
                # Handle directories we can't read
                error_prefix = prefix + ("" if is_root else ("    " if is_last else "│   "))
                tree_lines.append(f"{error_prefix}└── [Permission Denied]")

        except Exception as e:
            tree_lines.append(f"{prefix}└── [Error: {str(e)}]")

    add_directory_tree(start_path, is_root=True)
    return "\n".join(tree_lines)

def create_task_solve_workflow(problem_statement: str, test_cases: list, test_file: str, timeout: int = 2000) -> str:
    global run_id
    tool_manager = CreateTaskEnhancedToolManager(
        available_tools=[
            "get_file_content",
            "apply_code_edit",
            "run_test",
            "finish"
        ],
        test_file=test_file,
        test_cases=test_cases
    )

    system_prompt = CREATE_TASK_SYSTEM_PROMPT.format(
        tools_docs=tool_manager.get_tool_docs(),
        format_prompt=FORMAT_PROMPT_V0
    )

    instance_prompt = f"{problem_statement}\n# Test File Path\n\n{test_file}\n\n# Directory Structure\n{get_directory_tree()}"

    # Check if main.py exists and add it as a starting point
    if os.path.exists("main.py"):
        with open("main.py", "r") as f:
            main_content = f.read()
        instance_prompt += f"\n\n# Starting Point\n\nA main.py file already exists as a starting point for your implementation:\n\n```python\n{main_content}\n```"

    result, _, _, _ = execute_agent_workflow(
        problem_statement=problem_statement,
        timeout=timeout,
        run_id_1=run_id,
        instance_id="",
        models=[QWEN_MODEL_NAME],
        start_over_time=timeout,
        upgrade_model_time=timeout,
        tool_manager=tool_manager,
        system_prompt=system_prompt,
        instance_prompt=instance_prompt,
        max_steps=CREATE_TASK_SOLVE_MAX_STEPS,
        latest_observations_to_keep=1000,
        finish_tool_name="finish",
        log_prefix="CREATE_SOLVE_V0",
    )
    print(result)

    return result

def test_code_generation(problem_statement: str, test_cases: list) -> str:
    global run_id
    tool_manager = CreateTaskEnhancedToolManager(
        available_tools=[
            "get_file_content",
            "create_test_file",
            "apply_code_edit",
            "test_file_generation_finish"
        ],
        test_cases=test_cases
    )
    system_prompt = TEST_CODE_GENERATION_SYSTEM_PROMPT.format(
        tools_docs=tool_manager.get_tool_docs(),
        format_prompt=FORMAT_PROMPT_V0
    )
    instance_prompt = f"# Test Cases: \n\n{test_cases}\n# Directory Tree:\n{get_directory_tree()}"

    execute_agent_workflow(
        problem_statement=problem_statement,
        timeout=TEST_CODE_GENERATION_TIMEOUT,
        run_id_1=run_id,
        instance_id="",
        models=[GLM_MODEL_NAME],
        tool_manager=tool_manager,
        system_prompt=system_prompt,
        instance_prompt=instance_prompt,
        max_steps=TEST_CODE_GENERATION_MAX_STEPS,
        latest_observations_to_keep=1000,
        finish_tool_name="test_file_generation_finish",
        log_prefix="TEST_CODE_GENERATION"
    )

    return tool_manager.test_file

def test_case_extraction(problem_statement: str) -> list:
    retry = 0
    code_skeleton = get_code_skeleton()

    while retry < 10:
        try:
            temperature = random.uniform(0.0, 1.0)
            messages = [
                {"role": "system", "content": TEST_CASE_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"{problem_statement}\n# Code Skeleton:\n{code_skeleton} # Important\n - DO NOT CREATE TEST CASES NOT IN PROBLEM_STATEMENT\n - Make your test cases minimal"}
            ]

            response = EnhancedNetwork.make_request(messages, model=QWEN_MODEL_NAME, temperature=temperature)
            response=response.replace('```json','').strip('```').strip()
            print(response)
            if not response.endswith(']'):
                print("response incomplete, trying again")
                retry += 1
            else:
                break
        except Exception as e:
            retry = retry + 1

        time.sleep(2)


    if retry < 3:
        return Utils.load_json(response)

    return []

def process_fix_task(input_dict: Dict[str, Any]):
    """Main entry point for task processing and code modification.

    Parameters
    ----------
    input_dict : dict
        Configuration dictionary containing the task specification.
        Required key: 'problem_statement' with task details.
        Optional keys: 'run_id', 'instance_id' for tracking purposes.
    """
    # setting environment to include current working directory and lib directory
    global run_id
    problem_text = input_dict.get("problem_statement")
    if not problem_text:
        raise ValueError("input_dict must contain 'problem_statement'.")
    timeout = int(os.getenv("AGENT_TIMEOUT", str(DEFAULT_TIMEOUT)))

    logs = []
    patch_text = ""  # Initialize to avoid UnboundLocalError

    repo_path = os.getenv("REPO_PATH", "/sandbox/repo")
    repod_dir = repo_path.split('/')[-1]
    repod_path = repo_path[:-len(repod_dir)-1]
    if os.path.exists(repod_dir):
        os.chdir(repod_dir)

    set_env_for_agent()
    cwd = os.getcwd()
    logger.info(f"Current working directory: {cwd} and environ:{os.environ}")
    try:
        os.system("git init")
        os.system(f"git config --global --add safe.directory {repo_path}")
        os.system(f"git config --global --add safe.directory {repod_path}")
        os.system("git add .")
        os.system("git config user.email agent@abstract-runner.local")
        os.system("git config user.name 'Abstract Agent Runner'")
        os.system("git commit -m 'Initial commit'")
        logger.info(f"current files:{os.listdir()}")
        logger.info(f"packages installed:{subprocess.check_output(['pip','list']).decode('utf-8')}")
        logger.info(f"About to execute workflow...")
        patch_text= fix_task_solve_workflow(
            problem_text,
            timeout=timeout,
            run_id_1=run_id,
            instance_id=input_dict.get("instance_id", "")
        )
        logger.info(f"workflow execution completed, patch length: {len(patch_text)}")

        os.system("git reset --hard")

    except Exception as e:
        import traceback  # Ensure traceback is accessible
        error_info = f"Error: {e}, {traceback.format_exc()}"
        logger.error(f"[CRITICAL] Exception in task processing: {error_info}")
        logs.append(error_info)
    finally:
        os.chdir(cwd)

    print(f"[CRITICAL] task processor returning patch length: {len(patch_text)}")
    print(f"[CRITICAL] patch: {patch_text}")
    return patch_text

def fix_task_solve_workflow(problem_statement: str, *, timeout: int, run_id_1: str, instance_id: str = "") -> tuple[str, List[str], List[str]]:
    global run_id
    run_id=run_id_1
    cot=EnhancedCOT()
    tool_manager=FixTaskEnhancedToolManager(
        available_tools=[
            "get_file_content",
            "save_file",
            "get_functions",
            "get_classes",
            "search_in_all_files_content",
            "search_in_specified_file_v2",
            "start_over",
            "run_code",
            "apply_code_edit",
            "finish"
        ]
    )
    logger.info(f"Startingmain agent execution...")
    system_prompt = FIX_TASK_SYSTEM_PROMPT.format(tools_docs=tool_manager.get_tool_docs(),format_prompt=FORMAT_PROMPT_V0)
    instance_prompt = FIX_TASK_INSTANCE_PROMPT_TEMPLATE.format(problem_statement=problem_statement)

    start_time = time.time()
    logs: List[str] = []
    logs.append(f"cwd: {os.getcwd()}")
    logger.info(f"Starting workflow execution with {MAX_FIX_TASK_STEPS} max steps: timeout: {timeout} seconds : run_id: {run_id}")

    for step in range(MAX_FIX_TASK_STEPS):
        logger.info(f"Execution step {step + 1}/{MAX_FIX_TASK_STEPS}")

        if time.time() - start_time > timeout:
            cot.add_action(EnhancedCOT.Action(next_thought="global timeout reached",next_tool_name="",next_tool_args={},observation="",is_error=True,inference_error_counter={},request_data=[]))
            break

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": instance_prompt},
        ]

        messages.extend(cot.to_str())

        messages.append({"role": "system", "content": STOP_INSTRUCTION})

        try:
            next_thought, next_tool_name, next_tool_args,raw_text,total_attempts,error_counter,messages = EnhancedNetwork.inference(messages, model=GLM_MODEL_NAME, run_id=run_id)
        except Exception as e:
            import traceback  # Ensure traceback is accessible
            error_msg=f"\n\nERROR: {repr(e)} {traceback.format_exc()}"
            logger.error(f"Inference error: {error_msg}")
            cot.add_action(EnhancedCOT.Action(next_thought=error_msg,next_tool_name="",next_tool_args={},observation="",is_error=True,raw_response=raw_text,total_attempts=total_attempts),inference_error_counter=error_counter,request_data=messages)
            break

        logger.info(f"About to execute operation: {next_tool_name}")

        try:
            logger.info(f"next_thought: {next_thought}\nnext_tool_name: {next_tool_name}\nnext_tool_args: {next_tool_args}\n")
            if '"' in next_tool_name or "'" in next_tool_name:
                next_tool_name=next_tool_name.replace('"','')
                next_tool_name=next_tool_name.replace("'","")

            next_observation = tool_manager.get_tool(next_tool_name)(**next_tool_args) if next_tool_args else tool_manager.get_tool(next_tool_name)()
            logger.info(f"next_observation: {next_observation}")
            cot.add_action(EnhancedCOT.Action(next_thought=next_thought,next_tool_name=next_tool_name,next_tool_args=next_tool_args,observation=next_observation,is_error=False,raw_response=raw_text,total_attempts=total_attempts,inference_error_counter=error_counter,request_data=messages))
        except EnhancedToolManager.Error as e:
            import traceback  # Ensure traceback is accessible
            error_msg=f"observation: {e.message}"
            logger.error(f"Tool error: {error_msg}")
            cot.add_action(EnhancedCOT.Action(next_thought=next_thought,next_tool_name=next_tool_name,next_tool_args=next_tool_args,observation=error_msg,is_error=True,raw_response=raw_text,total_attempts=total_attempts,inference_error_counter=error_counter,request_data=messages))
            continue
        except Exception as e:
            import traceback  # Ensure traceback is accessible
            error_traceback=traceback.format_exc()
            if isinstance(e,TypeError):
                error_msg=f"observation: {str(e)}"
            else:
                error_msg=f"observation: {repr(e)} {error_traceback}"
            logger.error(f"Tool error: {error_msg}")
            cot.add_action(EnhancedCOT.Action(next_thought=next_thought,next_tool_name=next_tool_name,next_tool_args=next_tool_args,observation=error_msg,is_error=True,raw_response=raw_text,total_attempts=total_attempts,inference_error_counter=error_counter,request_data=messages))
            continue

        if next_tool_name == "finish" == next_observation:
            logger.info('[CRITICAL] Workflow called finish operation')
            break
        print(f"[CRITICAL] Completed step {step + 1}, continuing to next step")
    else:
        # This happens if we exit the loop without breaking (reached MAX_STEPS)
        cot.add_action(EnhancedCOT.Action(next_thought="global timeout reached",next_tool_name="",next_tool_args={},observation="",is_error=True))
        logger.info(f"[CRITICAL] Workflow completed after reaching MAX_STEPS ({MAX_FIX_TASK_STEPS})")

    logger.info(f"[CRITICAL] Workflow execution completed after {step + 1} steps")
    logger.info(f"[CRITICAL] About to generate final patch...")
    patch = tool_manager.get_final_git_patch()
    logger.info(f"Final Patch Generated..: Length: {len(patch)}")

    return patch