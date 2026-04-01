import chromadb

# Initialize the persistent ChromaDB client
client = chromadb.PersistentClient(path="./chroma_data")

# Get the codebase_nodes collection
collection = client.get_collection(name="codebase_nodes")

print(f"Collection '{collection.name}' has {collection.count()} documents.\n")
print("=" * 60)

# Perform a semantic query
query = "How is the final payment processed and tax added?"
print(f"Query: '{query}'")
print("=" * 60)

results = collection.query(
    query_texts=[query],
    n_results=2
)

ids       = results["ids"][0]
documents = results["documents"][0]
metadatas = results["metadatas"][0]
distances = results["distances"][0]

for i, (func_id, doc, meta, dist) in enumerate(zip(ids, documents, metadatas, distances), start=1):
    print(f"\n--- Result #{i} ---")
    print(f"  Function Name : {func_id}")
    print(f"  Author        : {meta.get('author', 'Unknown')}")
    print(f"  File          : {meta.get('filepath', 'N/A')}")
    print(f"  Start Line    : {meta.get('start_line', 'N/A')}")
    print(f"  Distance      : {dist:.4f}")
    print(f"  Code Snippet  :")
    print("-" * 40)
    print(doc)
    print("-" * 40)
