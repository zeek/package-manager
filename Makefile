VERSION=`cat VERSION`

.PHONY: all
all:

.PHONY: doc
doc: man html

.PHONY: man
man:
	(cd doc && make man && mkdir -p man && cp _build/man/bro-pkg.1 man)

.PHONY: html
html:
	(cd doc && make html)

.PHONY: livehtml
livehtml:
	(cd doc && make livehtml)

.PHONY: gh-pages
gh-pages:
	(cd doc && make clean html)
	(cd doc/_build/html && tar -czf /tmp/bro-pkg-html.tar.gz .)
	git checkout gh-pages
	git rm -rf . && git clean -fdx
	tar -xzf /tmp/bro-pkg-html.tar.gz && rm /tmp/bro-pkg-html.tar.gz
	git add .
	@echo "You are now in the 'gh-pages' branch."
	@echo "Make sure 'git status' looks ok, push, then switch back to 'master'"

.PHONY: test
test:
	@( cd testing && make )

.PHONY: upload
upload: twine-check
	python setup.py bdist_wheel
	twine upload -u bro dist/bro_pkg-$(VERSION)-py2.py3-none-any.whl

.PHONY: twine-check
twine-check:
	@type twine > /dev/null 2>&1 || \
		{ \
		echo "Uploading to PyPi requires 'twine' and it's not found in PATH."; \
		echo "Install it and/or make sure it is in PATH."; \
		echo "E.g. you could use the following command to install it:"; \
		echo "\tpip install twine"; \
		echo ; \
		exit 1; \
		}
