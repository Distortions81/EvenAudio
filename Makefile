CC ?= cc
CFLAGS ?= -O3 -Wall -Wextra -Werror -std=c11

all: libevenaudio.so

libevenaudio.so: dsp.c
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $< -lm

test: all
	python3 -m unittest discover -s tests -v

run: all
	python3 evenaudio.py

install-desktop: all
	python3 install_desktop.py --desktop

clean:
	rm -f libevenaudio.so

.PHONY: all test run install-desktop clean
