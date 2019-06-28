import click
from packit_service.worker.whitelist import Blacklist

"""
This is cli script to add user manually to blacklist when he violates our policies
"""


@click.group()
def cli():
    pass


@click.command("add")
@click.argument("account_name", type=str)
@click.option("--reason", type=str, required=True)
def add(account_name, reason):
    blacklist = Blacklist()
    if blacklist.add_account(account_name, reason):
        print(f"Account: {account_name} approved successfully")


@click.command("remove")
@click.argument("account_name", type=str)
def remove(account_name):
    blacklist = Blacklist()

    if blacklist.remove_account(account_name):
        print(f"Account: {account_name} removed from blacklist!")
    else:
        print(f"Account: {account_name} does not exists!")


cli.add_command(add)
cli.add_command(remove)

if __name__ == "__main__":
    cli()
