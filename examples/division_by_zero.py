# This script intentionally errors for demonstration
def divide(a, b):
    return a / b

if __name__ == "__main__":
    print("About to divide by zero...")
    print(divide(1, 0))