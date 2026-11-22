def add_numbers(a, b):
    """
    Adds two numbers.
    """
    return a + b

def calculate_tax(amount, rate):
    """
    Calculates tax by calling add_numbers.
    """
    tax_value = amount * rate
    return add_numbers(amount, tax_value)

def process_payment(amount):
    """
    Processes payment by calling calculate_tax.
    """
    final_amount = calculate_tax(amount, 0.05)
    print(f"Processing payment: {final_amount}")
    return final_amount

def greet(name):
    """
    Greets the given name.
    """
    print(f"Hello, {name}!")

def multiply_numbers(a, b):
    """
    Multiplies two numbers.
    """
    return a * b

class Calculator:
    def __init__(self):
        self.result = 0

    def subtract(self, a, b):
        return a - b
