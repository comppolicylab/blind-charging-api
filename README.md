Blind Charging API
===

A REST API for running the Blind Charging document processing pipeline.

## Deployment

Follow the instructions in the [`terraform`](./terraform/README.md) directory to deploy the Blind Charging API to Azure.

### Testing

The following steps will verify that the API is deployed correctly.

#### 0. Where is your API?

You will need to know what your `API Host` is in order to use it.
The host might be one of a few different values, depending on where you are going to connect to it.
Here are some common circumstances:
 - If you chose to expose your app to the public internet, the API is running behind an application gateway. You will use `https://<external IP>` for your API host, where `external IP` can be found in the Azure Portal under "Application Gateway" (the name will end with `-rbc-app-gw`). **NOTE** in the future we will support custom domains which you can use here instead of an IP address!
 - If your app is only available within a private vnet, you will need to run this command from somewhere that can reach the host. This may involve either setting up a custom rule on your vnet or setting up a VM you can SSH into (or both). In this case you can find the host value in the Azure Portal under the Container App UI's Overview tab. The `Application URL` will be something like `https://*-rbc-app.*.usgov*.azurecontainerapps.us`.
 - If you have no other options, you can go into the Azure Portal and navigate to "console" in the Container App UI. In this case, you will be using  `http://127.0.0.1:8000` as your host.


**NOTE** If you are using the external IP as your API host without a custom domain, you will need to use `https` but disable certificate validation on your client (whether that is your browser or `curl` or something else). With `curl` you can use the `-k` flag to bypass warnings from self-signed certificates.


#### 1. Simple `health` check

The API provides a health-check endpoint you can use to check if the API has started correctly.

```zsh
curl <api_host>/api/v1/health -D -
```

The server should respond with a `200 OK` message.


#### 2. Setting up client credentials (optional)

If you have not enabled the OAuth2 `client_credentials` flow on the API, you should skip this section.

First, need to provision a new Client ID and Client Secret to authenticate with the API.

To do so, open a bash shell in the `rbc-api`in the container in the "Console" tab of the Container App UI on the Azure Portal.
Then, run the following command:

```bash
python -m cli create-client '<your name>'
```

Replace `<your name>` with some reasonable identifier for this client.
(This is for bookkeeping purposes, the value is arbitary.)

The command will output a `Client ID` and `Client Secret`.
Keep these in a secure location -- they **cannot** be recovered after you leave this shell.
If you lose these values you will need to generate a new pair.

Now you can test the token endpoint by running the request:

```zsh
curl <api_host>/api/v1/oauth2/token -X POST -D - -H 'Content-Type: application/json' --data-binary @- << EOF
{
  "grant_type": "client_credentials",
  "client_id": "<your client ID",
  "client_secret": "<your client secret>"
}
EOF
```

The endpoint should respond to you with an access token and some metadata about it.
For more information about using this access token, see [here](https://auth0.com/docs/get-started/authentication-and-authorization-flow/client-credentials-flow/call-your-api-using-the-client-credentials-flow#response).


#### 3. Running a redaction request

This test will run an end-to-end document redaction.

The API comes shipped with a few PDF documents (from some incident reports that are publicly available from public records requests) that can be used for testing.

```zsh
curl <api_host>/api/v1/redact -k -X POST -D - -H 'Content-Type: application/json' --data-binary @- << EOF
{
  "jurisdictionId": "jur1",
  "caseId": "case2",
  "subjects": [
    {
      "role": "deceased",
      "subject": {
        "subjectId": "v1",
        "name": "Alphonza Watson"
      }
    }
  ],
  "objects": [
    {
      "document": {
        "attachmentType": "LINK",
        "documentId": "doc1",
        "url": "<api_host>/sample_data/hard.pdf"
      },
      "callbackUrl": "<your_callback_host>"
    }
  ]
}
EOF
```

**NOTE 1** In the following request, if you are using `client_credentials` or `preshared` authentication flows,
you will need to add an `Authorization` header to the request containing the token.
In `curl` this can be done with `-H 'Authorization: Bearer <access_token>'`.

**NOTE 2** That `your_callback_host` should be some server capable of receiving our response payload.
[`ngrok`](https://ngrok.com/) is a useful tool for development to make a local server in your development environment available to receive this callback,
but you can set this up however you wish.

**NOTE 3** Rather than using our sample documents,
it's better to pass a `url` to a PDF you host somewhere.
This will do a better job validating that the networking is configured correctly.


#### 4. Polling for data (optional)

We provide a `GET` endpoint for pulling redacted documents.

You may never use this endpoint if you are using callbacks to interact with our service (recommended),
so you may just skip testing it.

```zsh
curl <api_host>/api/v1/redact/jur1/case2 -D -
```

**NOTE** Remember to include the `Authorization` header if required.


#### 5. Testing the research endpoints (optional)

If your site is running research experiments,
you should also verify the other endpoints are working correctly.

**NOTE** Remember to include `Authorization` headers as necessary in all requests.

**Sample request to the `exposure` endpoint**

```zsh
curl <api_host>/api/v1/exposure -X POST -H 'Content-Type: application/json' -D - --data @- << EOF
{
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "protocol": "BLIND_REVIEW"
}
EOF
```

Should return null / `200` response.

**Sample request to the `outcome` endpoint**

```zsh
curl <api_host>/api/v1/outcome -X POST -H 'Content-Type: application/json' -D - --data @- << EOF
{
  "jurisdictionId": "jur1",
  "caseId": "case1",
  "subjectId": "sub1",
  "reviewingAttorneyMaskedId": "att1",
  "documentIds": ["doc1"],
  "decision": {
      "protocol": "BLIND_REVIEW",
      "outcome": {
          "disqualifyingReason": ["CASE_TYPE_INELIGIBLE", "OTHER"],
          "disqualifyingReasonExplanation": "This case should not have been selected for blind review.",
          "outcomeType": "DISQUALIFICATION"
      }
  },
  "timestamps": {
      "pageOpen": "2024-07-25T18:12:26.118Z",
      "decision": "2024-07-25T18:12:26.118Z"
  }
}
EOF
```

Should return null / `200` response.

**Sample request to the `blindreview` endpoint**

```zsh
curl <api_host>/api/v1/blindreview/jur1/case1 -D -
```

Should return `200` with info about blind review for this case.

#### 6. API Documentation

If you are able to access the api via a web browser (e.g., over VPN inside a vnet or if the app is exposed) you may find it useful to read the Swagger API documentation at `<api_host>/api/v1/docs`.
