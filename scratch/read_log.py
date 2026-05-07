import sys

def tail(filename, n=50):
    with open(filename, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        block_size = 1024
        res = []
        while size > 0 and len(res) <= n:
            if size - block_size > 0:
                f.seek(size - block_size)
                block = f.read(block_size)
                size -= block_size
            else:
                f.seek(0)
                block = f.read(size)
                size = 0
            lines = block.splitlines()
            if not res:
                res = lines
            else:
                res = lines + [res[0] + lines[-1]] + res[1:]
        
        # Simpler way
        f.seek(0)
        lines = f.readlines()
        return [l.decode('utf-8', errors='ignore').strip() for l in lines[-n:]]

if __name__ == "__main__":
    lines = tail(sys.argv[1])
    for l in lines:
        print(l)
