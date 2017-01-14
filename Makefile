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
