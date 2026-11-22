// sample_backend.java
package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class UserController {

    @GetMapping("/api/users")
    public String getUsers() {
        return "List of users";
    }

    @GetMapping("/api/user_data")
    public String getUserData(String userId) {
        String dbPassword = "SuperSecretPassword123!"; // Hardcoded password
        String query = "SELECT * FROM users WHERE id = " + userId; // SQL Injection Risk
        return "Executed " + query;
    }

    public void callOtherService() {
        // Just a dummy to test outgoing calls from backend
        String url = "/api/settings";
        String awsKey = "AKIA1234567890ABCDEF"; // Hardcoded AWS Key
        System.out.println("Calling " + url + " with " + awsKey);
    }
}
