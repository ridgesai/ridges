"""Shared Click / rich-click configuration for the Ridges CLI."""

from __future__ import annotations

import rich_click as click
from rich.text import Text
from rich_click import rich_click as rc

HELP_CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
}

rc.GROUP_ARGUMENTS_OPTIONS = False
rc.SHOW_ARGUMENTS = True
rc.USE_CLICK_SHORT_HELP = True
rc.MAX_WIDTH = 100
rc.TEXT_MARKUP = "rich"

rc.STYLE_USAGE = "bold cyan"
rc.STYLE_HELPTEXT = "white"
rc.STYLE_HELPTEXT_FIRST_LINE = "bold white"
rc.STYLE_OPTION = "bold cyan"
rc.STYLE_SWITCH = "bold cyan"
rc.STYLE_ARGUMENT = "bold white"
rc.STYLE_COMMAND = "bold cyan"
rc.STYLE_METAVAR = "dim white"
rc.STYLE_OPTION_HELP = "white"
rc.STYLE_COMMAND_HELP = "white"
rc.STYLE_OPTIONS_PANEL_BORDER = "cyan"
rc.STYLE_COMMANDS_PANEL_BORDER = "cyan"
rc.STYLE_ERRORS_PANEL_BORDER = "red"
rc.STYLE_ERRORS_SUGGESTION = "yellow"
rc.STYLE_HEADER_TEXT = "bold cyan"
rc.STYLE_FOOTER_TEXT = "dim"
rc.HEADER_TEXT = "╱╲╱╲╱╲  Ridges"
rc.FOOTER_TEXT = Text.assemble(("with ", "dim"), ("♥", "red"), (" from Latent", "dim"))

rc.COMMAND_GROUPS = {
    "* miner": [
        {
            "name": "Local miner workflow",
            "commands": ["setup", "run-local", "cleanup", "config"],
        }
    ],
    "* miner config": [
        {
            "name": "Config inspection",
            "commands": ["show"],
        }
    ],
}

rc.OPTION_GROUPS = {
    "* miner": [
        {
            "name": "Help",
            "options": ["--help"],
        }
    ],
    "* miner run-local": [
        {
            "name": "Task source",
            "options": ["--task-path", "--dataset", "--problem"],
        },
        {
            "name": "Run config",
            "options": [
                "--config-path",
                "--workspace",
                "--results-dir",
                "--agent-path",
                "--provider",
                "--debug",
                "--non-interactive",
                "--help",
            ],
        },
    ],
    "* miner setup": [
        {
            "name": "Setup options",
            "options": ["--config-path", "--help"],
        }
    ],
    "* miner cleanup": [
        {
            "name": "Cleanup options",
            "options": ["--config-path", "--workspace", "--results-dir", "--max-age-hours", "--dry-run", "--help"],
        }
    ],
    "* miner config show": [
        {
            "name": "Config options",
            "options": ["--config-path", "--help"],
        }
    ],
}


def format_help(summary: str, *examples: str) -> str:
    """Build a help string that rich-click renders consistently."""
    if not examples:
        return summary

    example_lines = "\n".join(f"[dim]› {example}[/dim]" for example in examples)
    return f"{summary}\n\n\b\n[dim]Examples[/dim]\n{example_lines}"


__all__ = ["HELP_CONTEXT_SETTINGS", "click", "format_help"]
