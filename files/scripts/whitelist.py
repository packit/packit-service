import click
from packit_service.worker.whitelist import Whitelist

"""
This is cli script to approve user manually after he installed github_app to repository
"""


@click.group()
def cli():
    pass


@click.command("approve")
@click.argument("account_name", type=str)
def approve(account_name):
    """
    Approve user who is waiting on whitelist.

    :param account_name: github namespace
    :return:
    """
    Whitelist().approve_account(account_name)


@click.command("remove")
@click.argument("account_name", type=str)
def remove(account_name):
    """
    Remove account from whitelist

    :param account_name: github namespace
    :return:
    """
    Whitelist().remove_account(account_name)


@click.command("waiting")
def waiting():
    """
    Show accounts waiting for approval.
    """
    print(f"Accounts waiting for approval: {', '.join(Whitelist().accounts_waiting())}")


cli.add_command(waiting)
cli.add_command(approve)
cli.add_command(remove)

if __name__ == "__main__":
    cli()
