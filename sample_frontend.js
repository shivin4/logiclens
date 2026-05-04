// sample_frontend.js
function getUserData() {
    console.log("Fetching user data...");
    fetch('/api/users')
        .then(response => response.json())
        .then(data => console.log(data));
}

const getSettings = () => {
    axios.get('/api/settings');
}
