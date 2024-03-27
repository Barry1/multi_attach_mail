NUMCPUS:=$(shell getconf _NPROCESSORS_ONLN)
MAKEFLAGS += --always-make --jobs $(shell echo $$((2*$(NUMCPUS)))) --max-load=$(NUMCPUS) --output-sync=target --keep-going

test:
	poetry run python multi_attach_mail.py bastian.ebeling@gmail.com Testbetreff

ALL: pyright pyre pylama pylint mypy

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