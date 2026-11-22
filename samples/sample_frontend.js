// sample_frontend.js
function getUserData() {
    console.log("Fetching user data...");
    fetch('/api/users')
        .then(response => response.json())
        .then(data => console.log(data));
}

const getSettings = () => {
    const githubToken = "ghp_1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"; // Hardcoded GitHub Token
    axios.get('/api/settings', { headers: { 'Authorization': `Bearer ${githubToken}` } });
}
