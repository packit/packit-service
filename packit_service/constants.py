EMAIL_TEMPLATE = """
Hello packit team!

There is good news and bad news. The good news is that someone wants to start using packit!

The bad ones are that automatic verification that user is a packager failed and you need to do it manually.

ACCOUNT INFORMATION:
User that installed application: {sender_login}
Account: {account_name}
Repositories: {repositories}

You can do that by following the steps below:
"""
