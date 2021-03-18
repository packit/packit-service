import click

from packit_service.worker.allowlist import Allowlist

PATH_HELP = (
    "Full path to be {} must be in the following format: github.com/packit or "
    "github.com/packit/packit.git for repository only"
)


"""
This is a CLI script to interact with our allowlist.
"""


@click.group()
def cli():
    pass


@cli.command(short_help="Approve namespace.", help=PATH_HELP.format("approved"))
@click.argument("full_path", type=str)
def approve(full_path):
    Allowlist().approve_namespace(full_path)


@cli.command(
    short_help="Remove namespace from allowlist. Removes the entry.",
    help=PATH_HELP.format("removed"),
)
@click.argument("full_path", type=str)
def remove(full_path: str):
    Allowlist().remove_namespace(full_path)


@cli.command(short_help="Show accounts waiting for an approval.")
def waiting():
    print("Accounts waiting for approval:")

    for namespace in Allowlist().accounts_waiting():
        print(f"- {namespace}")


if __name__ == "__main__":
    cli()
