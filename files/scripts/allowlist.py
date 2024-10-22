#!/usr/bin/env python3

# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
CLI script to interact with our allowlist.
"""

from typing import Optional
from urllib.parse import urlparse

import click

from packit_service.worker.allowlist import Allowlist

PATH_HELP = (
    "Full path to be {} must be in the following format: github.com/packit or "
    "github.com/packit/packit.git for repository only"
)


class RepoUrl(click.types.ParamType):
    name = "repo url"

    def __verify(self, path: str) -> bool:
        """
        Verifies if path is used with intention it has (domain vs namespace vs
        repository itself).

        Args:
            path: Repository or namespace URL.

        Returns:
            `True` if verified, `False` otherwise.
        """
        highlighted = "namespace"

        if "/" not in path:
            highlighted = "whole domain"
        elif path.endswith(".git"):
            highlighted = "specific repository"

        return click.confirm(
            "Are you manipulating with a "
            + click.style(highlighted, fg="red", bold=True)
            + f" ‹{path}›?",
        )

    def convert(
        self,
        value: Optional[str],
        param: Optional[click.Parameter] = None,
        ctx: Optional[click.Context] = None,
    ) -> Optional[str]:
        if not value:
            return None

        parsed_path = urlparse(value)
        if parsed_path.scheme:
            click.secho("Protocol ignored when allowlisting.", fg="yellow")
        result = f"{parsed_path.netloc}{parsed_path.path}"

        if not self.__verify(result):
            return None
        return result


def construct_path() -> str:
    """
    Constructs path from user input.

    Returns:
        Path from user input.
    """
    domain = click.prompt("Please input domain (e.g. github.com, gitlab.com)")
    namespace_or_repo = click.prompt(
        "(in case of specific repository, type the whole path suffixed with "
        "‹.git›, e.g. for ‹ogr› repository in ‹packit› namespace type "
        "‹packit/ogr.git›)\nPlease input namespace",
    )

    return "/".join((domain, namespace_or_repo))


def prompt_variant(path: str) -> Optional[str]:
    """
    Prompts user to choose from multiple options that can be allowlisted from a
    given URL.

    Args:
        path: URL to be allowlisted.

    Returns:
        Specific namespace or repository that is to be allowlisted. `None` if
        input is interrupted.
    """
    parts = path.count("/")

    for i in range(parts + 1):
        to_be_manipulated = path.rsplit("/", i)[0]
        click.echo(f"{i + 1}. {to_be_manipulated}")

    if choice := click.prompt(
        "Choose variant you want to allowlist",
        type=click.types.IntRange(min=1, max=parts + 1),
    ):
        return RepoUrl().convert(path.rsplit("/", choice - 1)[0])

    return None


@click.group()
def cli():
    pass


@cli.command(short_help="Approve namespace.", help=PATH_HELP.format("approved"))
@click.argument("full_path", type=RepoUrl(), required=False)
def approve(full_path: Optional[str]):
    if full_path is None:
        full_path = RepoUrl().convert(construct_path())

    is_approved_before = Allowlist.is_namespace_or_parent_approved(full_path)

    Allowlist.approve_namespace(full_path)
    if Allowlist.is_namespace_or_parent_approved(full_path) != is_approved_before:
        click.secho(f"Namespace ‹{full_path}› has been approved.", fg="green")
    else:
        click.secho(f"Status of namespace ‹{full_path}› has not changed.", fg="yellow")


@cli.command(short_help="Deny namespace.", help=PATH_HELP.format("denied"))
@click.argument("full_path", type=RepoUrl(), required=False)
def deny(full_path: Optional[str]):
    if full_path is None:
        full_path = RepoUrl().convert(construct_path())

    is_denied_before = Allowlist.is_denied(full_path)
    if is_denied_before:
        click.secho(f"Namespace ‹{full_path}› already denied.", fg="yellow")
        return

    Allowlist.deny_namespace(full_path)
    click.secho(f"Namespace ‹{full_path}› has been denied.", fg="green")


@cli.command(
    short_help="Remove namespace from allowlist. Removes the entry.",
    help=PATH_HELP.format("removed"),
)
@click.argument("full_path", type=RepoUrl())
def remove(full_path: str):
    if full_path is None:
        full_path = RepoUrl().convert(construct_path())

    Allowlist.remove_namespace(full_path)


@cli.command(short_help="Show accounts waiting for an approval.")
@click.pass_context
def waiting(ctx):
    click.echo("Accounts waiting for approval:")

    waiting_list = Allowlist.waiting_namespaces()
    for i, namespace in enumerate(waiting_list, 1):
        click.echo(f"{i}. {namespace}")

    if choice := click.prompt(
        f"Approve request number (1-{len(waiting_list)})",
        type=click.types.IntRange(min=min(1, len(waiting_list)), max=len(waiting_list)),
    ):
        click.echo()
        ctx.invoke(approve, full_path=prompt_variant(waiting_list[choice - 1]))


@cli.command(short_help="Show namespaces that are denied.")
def denied():
    click.echo("Denied namespaces:")

    denied_list = Allowlist.denied_namespaces()
    for i, namespace in enumerate(denied_list, 1):
        click.echo(f"{i}. {namespace}")


if __name__ == "__main__":
    cli()
