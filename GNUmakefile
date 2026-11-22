# Plain `make` from PowerShell / cmd on Windows builds with src/Makefile.mingw
# (32-bit MinGW, wxWidgets 3.1.3 and SDL2 2.0.12 from ../Libs). Elsewhere it
# falls through to the autotools Makefile. GNU make reads this before Makefile.
ifeq ($(OS),Windows_NT)

MINGW32_BIN := C:/msys64/mingw32/bin

ifeq ($(wildcard $(MINGW32_BIN)/gcc.exe),)
$(error No i686 MinGW gcc at $(MINGW32_BIN). Install it from an MSYS2 shell with: pacman -S --needed mingw-w64-i686-gcc mingw-w64-i686-expat mingw-w64-i686-zlib   (see START_HERE.md))
endif

SHELL := C:/Program Files/Git/usr/bin/sh.exe
export PATH := C:\msys64\mingw32\bin;C:\Program Files\Git\usr\bin;$(PATH)

.PHONY: all clean
all:
	$(MAKE) -C src -f Makefile.mingw
clean:
	$(MAKE) -C src -f Makefile.mingw clean

else
include Makefile
endif
