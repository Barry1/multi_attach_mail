.PHONY: all clean test
NUMCPUS=$(shell getconf _NPROCESSORS_ONLN)
MAKEFLAGS += --always-make --jobs $(shell echo $$((2*$(NUMCPUS)))) --max-load=$(NUMCPUS) --output-sync=target --keep-going

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
