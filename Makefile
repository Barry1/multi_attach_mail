.PHONY: all clean test sourcery pyrefly
NUMCPUS = $(shell getconf _NPROCESSORS_ONLN)
MAKEFLAGS += --always-make --jobs $(shell echo $$((2*$(NUMCPUS)))) --max-load=$(NUMCPUS) --output-sync=target --keep-going

normalize:
	poetry run isort --profile=black multi_attach_mail.py
	poetry run ruff format multi_attach_mail.py
	poetry run ruff check multi_attach_mail.py --fix

pyupgrade:
	poetry run pyupgrade --py313-plus multi_attach_mail.py

wemake-python-styleguide:
	poetry run flake8 multi_attach_mail.py --select=WPS

pyrefly:
	poetry run pyrefly check

sourcery:
	poetry run sourcery review --fix --summary --verbose .

test:
	poetry run python multi_attach_mail.py bastian.ebeling+multi_attach_mail@gmail.com "Test aus Makefile"

all: pyright pyre pylama pylint mypy

pyre:
	poetry run pyre

pyright:
	poetry run pyright multi_attach_mail.py

pylama:
	poetry run pylama multi_attach_mail.py

pylint:
	poetry run pylint multi_attach_mail.py

mypy:
	poetry run mypy multi_attach_mail.py

megalinter:
	npx mega-linter-runner -e "'ENABLE=MARKDOWN,YAML'" -e 'SHOW_ELAPSED_TIME=true'

megalinterflavored:
	npx mega-linter-runner --flavor python -e "'ENABLE=MARKDOWN,YAML'" -e 'SHOW_ELAPSED_TIME=true'

ruff:
	poetry run ruff format multi_attach_mail.py

vermin:
	poetry run vermin multi_attach_mail.py