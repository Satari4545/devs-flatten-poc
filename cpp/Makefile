# Builds the adevs C++ ping pong. Needs the adevs submodule:
#   git submodule update --init --recursive
CXX = g++
CXXFLAGS = -std=c++17 -Wall -Iadevs/include

pingpong: pingpong.cpp adevs/src/adevs.cpp
	$(CXX) $(CXXFLAGS) pingpong.cpp adevs/src/adevs.cpp -o pingpong

run: pingpong
	./pingpong

clean:
	rm -f pingpong

.PHONY: run clean
