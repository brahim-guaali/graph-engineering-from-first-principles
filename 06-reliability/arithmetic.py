"""Why structure makes answers more reliable, in a few lines of arithmetic.

    python 06-reliability/arithmetic.py
"""


def chain(p, n):
    """Chance that n decisions in a row are all right, if each is right with probability p."""
    return p ** n


def with_retries(p, attempts):
    """Chance a node gets it right within `attempts` tries, if a sound check catches every miss
    and the tries are independent (never perfectly true, but it shows the shape)."""
    return 1 - (1 - p) ** attempts


if __name__ == "__main__":
    print("1. Reliability decays exponentially with the number of uncertain decisions in a row:")
    for n in (1, 4, 10, 30, 60, 100):
        print(f"   {n:3d} decisions at 99% each -> {chain(0.99, n):6.1%} of runs get through")

    print("\n2. A graph lowers the number of uncertain decisions. In our refund the step order, the")
    print("   $500 rule, the wait for approval and the payment are code. What's left for the model:")
    print(f"   loop:  ~7 choices (every tool call and the stop)  -> {chain(0.99, 7):.1%}")
    print(f"   graph:  2 judgments (classify, decide)            -> {chain(0.99, 2):.1%}")

    print("\n3. A narrow node with a sound check can retry. A judgment that is right 90% of the time:")
    for k in (1, 2, 3):
        print(f"   {k} attempt{'s' if k > 1 else ' '} -> {with_retries(0.90, k):.1%}  "
              f"(fails {1 - with_retries(0.90, k):.3%} of the time)")

    print("\n4. And a narrow node reads less: our graph's decide reads ~550 tokens; the loop's last")
    print("   call read ~1,750. Model accuracy tends to drop as the input gets longer.")
