# student3.py
import sys
from student_common import main

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python student3.py <HOST> <PORT>")
        sys.exit(1)
    main("3", sys.argv[1], int(sys.argv[2]))
