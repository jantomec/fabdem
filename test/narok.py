import fabdem
from pathlib import Path


def test():
    bounds = (35.88, 1.09, 39.56, 7.91)
    output_path = "dem.tif"
    fabdem.download(bounds, output_path)
    return Path(output_path).exists()

if __name__ == "__main__":
    result = test()
    if result:
        print("PASS")
    else:
        print("FAIL")