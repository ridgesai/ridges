const fs = require('fs');
const path = require('path');
const {expect} = require('expect');



const repoPath = "/sandbox/repo";



function runTests() {
    global.testResults = [];



    // Jest Emulator
    global.describe = function(description, callback) {
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] describe(): "${description}"`);
        callback();
    };
    
    global.xdescribe = function(description, callback) {
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] xdescribe(): "${description}"`);
        describe(description, callback);
    };

    global.test = function(description, callback) {
        console.log(`[POLYGLOT_TEST_RUNNER] [JEST] Test: "${description}"...`);
        try {
            callback();
            console.log(`[POLYGLOT_TEST_RUNNER] [JEST] Test passed: "${description}"`);
            global.testResults.push({
                "name": description,
                "category": "default",
                "status": "pass"
            });
        } catch (e) {
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

    global.expect = expect;



    console.log("[POLYGLOT_TEST_RUNNER] Loading tests.js");
    require(path.join(repoPath, "tests.js"));
    console.log("[POLYGLOT_TEST_RUNNER] Loaded tests.js");
    
    return global.testResults;
}



function main() {
    console.log("[POLYGLOT_TEST_RUNNER] Entered main()");
    
    try {
        const testResults = runTests();

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