#!/usr/bin/env bash
set -eo pipefail

# Script to run validator, with auto-updates
# Usage:
# > ./scripts/run.sh <pm2 arguments> -- <validator.py arguments>
# Example:
# > ./scripts/run.sh --name ridges-validator -- --netuid 62 --wallet.name validator_wallet --wallet.hotkey default --logging.info

SCRIPT_DIR=$(dirname "$(realpath "$0")")
REPO_ROOT=$(dirname "$SCRIPT_DIR")
pushd $REPO_ROOT > /dev/null || exit 1

sleep_duration_s=300  # Sleep 5 minutes between update checks
branch="$(git branch --show-current)"
auto_update_enabled=${RIDGES_VALIDATOR_AUTO_UPDATE:-1}
stash_msg="autostash-pre-rebase"
stash_ref=""

DEFAULT_PROC_NAME="ridges-validator"  # in absence of --name flag

######## Extract --name flag ###########################
args=("$@")  # Copy arguments to a new array so that we can use shift
proc_name="$DEFAULT_PROC_NAME"
for ((i = 1; i <= $#; i++)); do
  arg="${!i}"
  next_index=$((i + 1))

  if [[ $arg == "--" ]]; then
    break
  elif [[ $arg == "--name" ]]; then
    next_arg="${!next_index}"
    proc_name="$next_arg"
  elif [[ $arg == --name=* ]]; then
    proc_name="${arg#--name=}"
  fi
done
##########################################################

# Stop old processes
if pm2 show "$proc_name" > /dev/null; then
  echo "Purging old $proc_name process..."
  pm2 stop "$proc_name" > /dev/null
  pm2 delete "$proc_name"
  echo "Finished purging old $proc_name process"
fi

function handle_interrupt() {
  echo "Stopping the validator pm2 process ($proc_name)..."
  pm2 stop "$proc_name"
  echo "Validator pm2 process '$proc_name' stopped"
  exit 1
}

# Set up the trap
trap handle_interrupt SIGINT


function branch_exists_on_remote() {
  local branch_name="$1"
  local remote="${2:-origin}" # Default to 'origin' if no remote is specified

  if git ls-remote --heads "$remote" "$branch_name" | grep -q "refs/heads/$branch_name"; then
    return 0  # true
  else
    return 1  # false
  fi
}

function is_validator_running() {
  pm2 jlist | jq -e "if length > 0 then .[] | select(.name == \"$proc_name\" and .pm2_env.status == \"online\") else empty end" > /dev/null
}

function run-post-update-processes() {
  python3 -m pip install -e .  # Reinstall in case of updated dependencies

  # Stop current out-of-date process
  if is_validator_running; then
    echo "Stopping current validator process ($proc_name)..."
    pm2 stop "$proc_name"
    echo "$proc_name stopped."
  fi
}

################## Construct pm2_start_command ############################
pm2_args=()
validator_args=()
delimiter_found=false
for arg in "${args[@]}"; do
  if [[ $arg == "--" ]]; then
      delimiter_found=true
  elif [[ $delimiter_found == false ]]; then
      pm2_args+=("$arg")
  else
      validator_args+=("$arg")
  fi
done

pm2_start_command="pm2 start neurons/validator.py"
# Append pm2_args only if non-empty
if [[ ${#pm2_args[@]} -gt 0 ]]; then
    pm2_start_command+=" ${pm2_args[*]}"
fi

# Append validator_args only if non-empty
if [[ ${#validator_args[@]} -gt 0 ]]; then
    pm2_start_command+=" -- ${validator_args[*]}"
fi
echo "pm2 start command is: '$pm2_start_command'"
#########################################################

if ! branch_exists_on_remote "$branch"; then
  echo "Branch 'branch' does not exist on remote, running without auto-update..."
  auto_update_enabled=0
fi

if [[ $auto_update_enabled = 0 ]]; then
  echo "Running with auto-update disabled..."
fi

while true; do
  status_update_msg=""
  git fetch -q origin "$branch"
  remote_version=$(git rev-parse origin/"$branch")

  local_version=$(git rev-parse "$branch")

  # Is auto-update on and is there a corresponding branch on origin?
  if [[ $auto_update_enabled = 1 ]] && ! git merge-base --is-ancestor origin/"$branch" "$branch"; then
    echo "Local code is out of date (sha $local_version), attempting update to ($remote_version)..."

    # Unstaged changes exist, apply rebase + stash
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "WARNING: Unstaged changes detected. Attempting to update via auto-stashing the changes and reapplying them..."

      # Attempt to stash and auto-update
      stash_ref=$(git stash push -m "$stash_msg")
      pre_rebase_commit="$(git rev-parse HEAD)"

      if git rebase --quiet origin/"$branch" &> /dev/null; then  # Rebase command succeeded
        echo "Rebase command succeeded, attempting to reapply stash..."

        if [ -n "$stash_ref" ]; then

          if git stash pop; then
            # Stash applied successfully
            run-post-update-processes
            echo "Unstaged changes restored successfully, and code has been updated..."

            status_update_msg="Successfully reapplied staged changes and rebased repo"
            echo "$status_update_msg"

          else
            # Stash failed to apply, revert to previous state and request manual changes
            git reset --hard "$pre_rebase_commit"
            if [ -n "$stash_ref" ]; then
              git stash pop
            fi

            status_update_msg="ERROR: Auto-update aborted. Failed to apply stashed changes due to unstaged changes causing merge conflicts. Please update manually via \`git pull\`."
            echo "$status_update_msg"
          fi

        fi

      else  # Rebase command failed, revert back to previous state with unstaged changes
        git rebase --abort
        git reset --hard "$pre_rebase_commit"

        if [ -n "$stash_ref" ]; then
          git stash pop
        fi

        status_update_msg="ERROR: Auto-update aborted due to the \`git rebase\` command failing. Please update manually via \`git pull\`."
        echo "$status_update_msg"
      fi

    # No unstaged changed, apply rebase directly
    elif git rebase --quiet origin/"$branch" &> /dev/null; then
      echo "Updated branch successfully"
      run-post-update-processes

    # Applying rebase directly failed
    else
      git rebase --abort

      status_update_msg="ERROR: Auto-update aborted due to the \`git rebase\` command failing. Please update manually via \`git pull\`."
      echo "$status_update_msg"
    fi
  else
    echo "Code is up to date. Running commit $(git rev-parse HEAD) on branch \`$branch\`"
  fi

  # Restart validator process if needed
  if ! is_validator_running; then
    echo "Starting validator via command: '$pm2_start_command'"
    eval "$pm2_start_command"
    echo "Process started, run \`pm2 monit\` to see progress"
  else
    echo "Validator is already running, skipping restart..."
    pm2 list
  fi

  echo "Sleeping for $sleep_duration_s seconds while validator script is running. Keep this script running, stopping it will stop the validator..."
  echo "$status_update_msg"
  sleep $sleep_duration_s
done

popd > /dev/null
