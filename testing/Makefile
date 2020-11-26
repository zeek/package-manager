.PHONY: all
all: btest-installation-check
	@btest -j -f diag.log

.PHONY: btest-installation-check
btest-installation-check:
	@type btest > /dev/null 2>&1 || \
		{ \
		echo "btest was not located in PATH."; \
		echo "Install it and/or make sure it is in PATH."; \
		echo "E.g. you could use the following command to install it:"; \
		echo "\tpip3 install btest"; \
		echo ; \
		exit 1; \
		}
