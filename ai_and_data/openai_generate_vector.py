import openai

# Azure OpenAI settings
openai.api_type = "azure"
openai.api_key = "YOUR_API_KEY"  # Replace with your actual key
openai.azure_endpoint = "https://YOUR_AZURE_OPENAI_ENDPOINT/"  # Replace with your endpoint
openai.api_version = "2023-05-15"  # Use the version shown in your Azure resource

text = "Mountains are beautiful in the fall."
response = openai.embeddings.create(
    input=[text],
    model="ada-embedding-deployment"  # Replace with your deployment name
)
embedding = response.data[0].embedding
print(embedding)