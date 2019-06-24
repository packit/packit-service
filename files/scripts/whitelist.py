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
    whitelist = Whitelist()
    if whitelist.approve_account(account_name):
        print(f"Account: {account_name} approved successfully")
    else:
        print(f"Account: {account_name} does not exists or it is already approved")


@click.command("remove")
@click.argument("account_name", type=str)
def remove(account_name):
    whitelist = Whitelist()

    if whitelist.remove_account(account_name):
        print(f"Account: {account_name} removed from whitelist!")
    else:
        print(f"Account: {account_name} does not exists!")


cli.add_command(approve)
cli.add_command(remove)

if __name__ == "__main__":
    cli()
