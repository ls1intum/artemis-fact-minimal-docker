CC = gcc
CFLAGS = -std=c11 -Wall
STRICT_FLAGS = -std=c11 -Wall -Werror -pedantic

.PHONY: main strict clean

main: main.c
	$(CC) $(CFLAGS) -o main main.c

strict: main.c
	$(CC) $(STRICT_FLAGS) -o main main.c

clean:
	rm -f main
