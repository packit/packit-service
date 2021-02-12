#!/bin/bash

set -eu

TOOLBOX_NAME=$1

(toolbox list | grep -q $TOOLBOX_NAME) || {
	toolbox create -c $TOOLBOX_NAME
	toolbox run -c $TOOLBOX_NAME sudo make setup-toolbox
}

toolbox run -c $TOOLBOX_NAME make check
