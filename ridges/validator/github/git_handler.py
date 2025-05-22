import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict

from dotenv import load_dotenv
from github import Github, Auth
from github.PullRequest import PullRequest
from github.Repository import Repository
from pydantic import BaseModel

from agentao.repo_environment import SUPPORTED_OPEN_REPOS
from sweagent.environment.swe_env import EnvironmentArguments, SWEEnv

load_dotenv()

# TODO: How to make less github calls

class PrMetadata(BaseModel):
    agent: str
    issue_num: int
    validator: str

@dataclass
class GitHubOpenIssue:
    repo: str  # Format: author/repo-name
    author: str
    title: str
    issue_num: int
    problem_statement: str
    base_commit: str
    repo_url: str
    issue_url: str
    required_tests: List[str]

class GitHubIssueHandler:
    repo_map: Dict[str, Repository]
    def __init__(self):
        auth = Auth.Token(os.getenv("GITHUB_TOKEN"))
        self.github = Github(auth=auth)
        self.repo_map = {}

        for repo in SUPPORTED_OPEN_REPOS:
            try:
                repo_obj = self.github.get_repo(repo)
            except Exception as e:
                print(f"Error: {e}")
                continue

            self.repo_map[repo] = repo_obj

    def get_open_issues(self, repo) -> List[GitHubOpenIssue]:
        """
        Get ALL issues such that
        1. It is a normal issue that is marked as open, with no associated PRs.
        2. It is a pull request with no pending draft PRs or code waiting to be reviewed.
        3. It is an issue w
        """   
        if repo not in self.repo_map:
            return []
        
        pull_requests = self.repo_map[repo].get_pulls(state='open')
        issues = []
        for issue in self.repo_map[repo].get_issues(state='open'):
            is_issue_good = False
            required_tests: List[str] = []

            # TODO: Amend check to check for Open PRs only
            if any(tl_event.event == "cross-referenced" for tl_event in issue.get_timeline()):
                print(f"Skipping issue {issue} because it has existing PRs")
                continue

            # Ignore issues which are pull requests (get_issues call returns both)
            if issue.pull_request:
                continue

            # Check if 
            comments = issue.get_comments()
            for comment in comments:
                # TODO: What if they want to cancel the submit later on
                if comment.body.lower() == "@taoagents submit" and comment.user.login == issue.user.login:
                    is_issue_good = True
                elif comment.body.startswith("@taoagents required_tests"):
                    pattern = r"^@taoagents required_tests ((?:\w+|file:\w+)(?:,\s*(?:\w+|file:\w+))*)$"
                    re_match = re.match(pattern, comment.body)
                    if re_match:
                        tests = re_match.group(1).split(",")  # Extract and split by comma
                        required_tests = [test.strip() for test in tests]  # Trim spaces

            # Check if a PR already exists for it
            for pr in pull_requests:
                if pr.body:
                    lines = pr.body.split("\n")
                    lines = [line.strip() for line in lines]

                    submit_line = f"@taoagents closes #{issue.number}"

                    if submit_line in lines and pr.state == 'open':
                        is_issue_good = False
                        break

            if is_issue_good:
                body = ["issue body: " + issue.body]
                body.extend(["comment: " + comment.body for comment in comments if "@taoagents" not in comment.body.lower()])
                body = "\n".join(body)
                issues.append(GitHubOpenIssue(
                    repo, 
                    issue.user.login, 
                    issue.title, 
                    issue.number, 
                    body,
                    self.repo_map[repo].default_branch, 
                    self.repo_map[repo].html_url,
                    issue.html_url,
                    required_tests
                ))
        return issues
    
    def select_random_issue(self) -> Optional[GitHubOpenIssue]:
        for repo in SUPPORTED_OPEN_REPOS:
            open_issues = self.get_open_issues(repo)
            if open_issues:
                return open_issues[0]
            
        return None
    
    def assign_rewards(self) -> Dict[str, int]:
        """
        Returns
        -------
        Dictionary of {miner_hotkey: number PRs}. If a hotkey
        is not in this dictionary, then they have no PRs to claim.
        """
        rewards = {}
        # For each open PR
        for repo in SUPPORTED_OPEN_REPOS:
            repo = self.repo_map[repo]
            pulls = repo.get_pulls(state='open', sort='created', base=repo.default_branch)

            # Check open PRs that have been accepted
            for pull in pulls:
                if not pull.body:
                    continue

                lines = pull.body.split("\n")
                lines: List[str] = [line.strip() for line in lines]
                lines = [x.replace("\r", "") for x in lines]
                lines = list(filter(None, lines))

                submit_line = "@taoagents closes #"
                agentao_mr = False
                for line in lines:
                    if line.startswith(submit_line):
                        agentao_mr = True
                        break
                
                if not agentao_mr: continue

                # Now we see if the PR has been accepted by the owner
                # within the last 24 hours
                owner_accepted = False
                accepted_time = None
                for comment in pull.get_issue_comments():
                    if comment.user.login == repo.owner.login:
                        if comment.body == "@taoagents accept":
                            now = datetime.now(tz=comment.created_at.tzinfo)
                            if (now > comment.created_at) and (now - comment.created_at).total_seconds() < 24 * 60 * 60:
                                accepted_time = comment.created_at
                                owner_accepted = True
                                break

                if not owner_accepted: continue

                # Now we parse the metadata
                for i, body_comm in enumerate(lines):
                    if body_comm == "</details>":
                        break
                metadata = lines[i-1]
                metadata = json.loads(metadata)
                
                try:
                    metadata = PrMetadata(
                        agent=metadata['agent'],
                        issue_num=metadata['issue_num'],
                        validator=metadata['validator'],
                    )
                except Exception as e:
                    print("Exception deserializing metadata: ", e)
                    continue

                issue = repo.get_issue(metadata.issue_num)

                issue_verified = False

                for comment in issue.get_comments():
                    if comment.body.lower() == "@taoagents submit" and \
                       comment.user.login == issue.user.login and \
                       comment.created_at < accepted_time:
                        issue_verified = True
                        break

                if not issue_verified: continue

                # All good, lets reward them
                if metadata.agent not in rewards:
                    rewards[metadata.agent] = 0

                rewards[metadata.agent] += 1

        return rewards
    
    def open_pr(
        self, 
        patch: str, 
        open_issue: GitHubOpenIssue,
        metadata: PrMetadata,
    ) -> Optional[PullRequest]:
        try:
            script_args = EnvironmentArguments(
                image_name="sweagent/swe-agent:latest",
                data_path=f"text://{open_issue.issue_url}",
                repo_path=open_issue.repo_url,
                base_commit=open_issue.base_commit,
                verbose=True,
            )
            env = SWEEnv(script_args)
            env.reset()
            path_to_patch = "model.patch"
            with open(path_to_patch, "w") as f:
                f.write(patch)

            subprocess.run(
                f'docker cp {path_to_patch} {env.container_name}:/root/model.patch',
                shell=True,
                check=False,
            )
            env.communicate_with_handling(
                input="git apply /root/model.patch",
                error_msg="Failed to apply patch correctly",
            )
            os.remove(path_to_patch)
            metadata = metadata.model_dump_json()
            pr = env.open_pr(trajectory="", _dry_run=False, metadata=metadata)
            return pr
        except Exception as e:
            print(f"Error: {e}")
            return None

if __name__ == "__main__":
    gh = GitHubIssueHandler()

    # issues = gh.select_random_issue()

    # print(issues)

    # # Read patch file from diff.txt
    # parent_dir = os.path.dirname(os.path.abspath(__file__))
    # with open(parent_dir+"/diff.txt", "r") as f:
    #     patch = f.read()

    # gh.open_pr(
    #     patch,
    #     issues,
    #     metadata='{"agent": "F2aPa32nVhjv8e2wn", "issue_num": 5, "validator": "F2m38gsanegbi"}',
    # )

    metadata = PrMetadata(
        agent="F2aPa32nVhjv8e2wn",
        issue_num=5,
        validator="F2m38gsanegbi",
    )

    print(gh.assign_rewards())