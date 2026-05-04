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

    public void callOtherService() {
        // Just a dummy to test outgoing calls from backend
        String url = "/api/settings";
        System.out.println("Calling " + url);
    }
}
