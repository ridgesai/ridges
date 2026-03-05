/**
 * Polyglot Test Runner - JavaScript Version
 * 
 * This script runs inside the Docker sandbox to execute JavaScript tests
 * for the polyglot problem suite. It provides a Jest-like testing environment
 * and runs tests defined in /sandbox/repo/tests.js.
 * 
 * Execution Flow:
 * 1. Sets up Jest-like globals (describe, test, expect, beforeEach, afterEach)
 * 2. Loads and executes tests.js from the repository
 * 3. Collects test results as tests execute
 * 4. Writes structured results to output.json
 * 
 * Expected File Layout:
 *     /sandbox/repo/
 *         ├── main.js    # Code to test
 *         └── tests.js   # Jest-style test definitions
 * 
 * Jest-like API Provided:
 *     - describe(name, fn): Define a test suite
 *     - test(name, fn): Define a test case
 *     - expect(value): Assertion interface
 *     - beforeEach(fn): Setup hook
 *     - afterEach(fn): Teardown hook
 * 
 * Output Format:
 *     [
 *         {"name": "test description", "category": "default", "status": "pass|fail|skip"},
 *         ...
 *     ]
 */

const fs = require('fs');
const path = require('path');
const {expect} = require('expect');



// Path to the code repository inside the sandbox
const repoPath = "/sandbox/repo";



function runTests() {
    /** Discover and run all tests in the repository. */
    
    // Array to collect test results as they execute
    global.testResults = [];



    // ============================================
    // Jest API Emulator
    // ============================================
    // Provides Jest-like globals for test files to use
    // This allows tests to be written in standard Jest style
    
    global._beforeEachCallbacks = [];
    global.beforeEach = function(callback) {
        /** Register a setup function to run before each test. */
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] beforeEach()`);
        global._beforeEachCallbacks.push(callback);
    };

    global._afterEachCallbacks = [];
    global.afterEach = function(callback) {
        /** Register a teardown function to run after each test. */
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] afterEach()`);
        global._afterEachCallbacks.push(callback);
    };

    global.describe = function(description, callback) {
        /** Define a test suite (group of related tests). */
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] describe(): "${description}"`);
        callback();
    };
    
    global.xdescribe = function(description, callback) {
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] xdescribe(): "${description}"`);
        describe(description, callback);
    };

    global.test = function(description, callback) {
        /** Define and immediately execute a test case.
         * 
         * Runs beforeEach hooks, the test, then afterEach hooks.
         * Captures success/failure and records the result.
         */
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] Test: "${description}"...`);
        try {
            // Run setup hooks
            for (const beforeEachCallback of global._beforeEachCallbacks)
                beforeEachCallback();
            
            // Execute the test
            callback();
            
            // Run teardown hooks
            for (const afterEachCallback of global._afterEachCallbacks)
                afterEachCallback();
            
            console.log(`[POLYGLOT_TEST_RUNNER] [JEST] Test passed: "${description}"`);
            global.testResults.push({
                "name": description,
                "category": "default",
                "status": "pass"
            });
        } catch (e) {
            // Test failed - capture the error
            console.log(`[POLYGLOT_TEST_RUNNER] [JEST] Test failed: "${description}"`);
            console.log(e);
            global.testResults.push({
                "name": description,
                "category": "default",
                "status": "fail"
            });
        }
    };

    global.xtest = function(description, callback) {
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] xtest(): "${description}"`);
        test(description, callback);
    };

    global.test.skip = function(description, callback) {
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] test.skip(): "${description}"`);
    };

    // Make expect available for assertions in tests
    global.expect = expect;



    // Load and execute the test file
    // This will trigger all the test() calls which run immediately
    console.log("[POLYGLOT_TEST_RUNNER] Loading tests.js");
    require(path.join(repoPath, "tests.js"));
    console.log("[POLYGLOT_TEST_RUNNER] Loaded tests.js");
    
    return global.testResults;
}



function main() {
    /** Main entry point for test execution inside the sandbox. */
    console.log("[POLYGLOT_TEST_RUNNER] Entered main()");
    
    try {
        // Run all tests and collect results
        const testResults = runTests();

        // Print results for debugging (visible in container logs)
        console.log("[POLYGLOT_TEST_RUNNER] Test results:");
        console.log(JSON.stringify(testResults, null, 2));
        
        const testsPassed = testResults.filter(test => test["status"] === "pass").length;
        const testsFailed = testResults.filter(test => test["status"] === "fail").length;
        const testsSkipped = testResults.filter(test => test["status"] === "skip").length;
        
        console.log(`[POLYGLOT_TEST_RUNNER] Test summary: ${testsPassed} passed, ${testsFailed} failed, ${testsSkipped} skipped`);

        console.log("[POLYGLOT_TEST_RUNNER] Writing output.json");
        fs.writeFileSync("/sandbox/output.json", JSON.stringify({
            "success": true,
            "output": testResults
        }, null, 2));
        console.log("[POLYGLOT_TEST_RUNNER] Wrote output.json");

    } catch (e) {
        console.log("[POLYGLOT_TEST_RUNNER] Exception:");
        console.log(e.stack || e);
        
        const output = {
            "success": false,
            "error": String(e),
            "traceback": e.stack || ""
        };
        
        try {
            console.log("[POLYGLOT_TEST_RUNNER] Writing output.json");
            fs.writeFileSync("/sandbox/output.json", JSON.stringify(output, null, 2));
            console.log("[POLYGLOT_TEST_RUNNER] Wrote output.json");
        } catch {
            console.log("[POLYGLOT_TEST_RUNNER] Failed to write output.json");
        }
    }

    console.log("[POLYGLOT_TEST_RUNNER] Exiting main()");
}



if (require.main === module) {
    main();
}