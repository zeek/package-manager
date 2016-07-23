.PHONY: doc
doc: man html

.PHONY: man
man:
	(cd doc && make man && mkdir -p man && cp _build/man/bro-pkg.1 man)

.PHONY: html
html:
	(cd doc && make html)
