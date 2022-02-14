import click

# we try to import local python file

import test


@click.command()
def hello():
    click.echo('Hello World!')

    test.test()


