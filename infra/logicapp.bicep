// ============================================================================
// Logic App (Consumption) – Event Grid Trigger → CUA API Pipeline
//
// Triggered by Microsoft.Storage.BlobCreated events from aaaorgcuastore,
// fired for JSON files uploaded to the `payloads` container under
// payloads/{YYYY}/{MM}/{DD}/{HH}/<filename>.json
//
// Flow:
//   1. Event Grid posts BlobCreated event to Logic App HTTP trigger
//   2. Handle Event Grid subscription validation handshake
//   3. Fetch blob content via MSI-authenticated HTTP GET
//   4. Detect mode (create / update) — mirrors _detect_mode() in app.py
//   5. Validate required fields + enum values (reject before touching the API)
//   6. POST /api/run  → receive job_id
//   7. Poll GET /api/status/{job_id}  (max 20 × 15 s = 5 min)
//   8. Write audit record to Azure Table Storage (logicAppAudit)
//
// Auth: System-Assigned Managed Identity on the Logic App.
//   – Storage Blob Data Reader  (fetch payload blobs via HTTP)
//   – Storage Table Data Contributor  (write audit records)
//   These role assignments live in logicapp-rbac.bicep.
//
// No API connections needed – fully keyless.
// ============================================================================

@description('Azure region')
param location string

@description('Unique resource token used for naming')
param resourceToken string

@description('Storage account name (aaaorgcuastore) – must already exist')
param storageAccountName string = 'aaaorgcuastore'

@description('CUA Container App base URL')
param cuaApiUrl string = 'https://cua-lunarair.braverock-d5a3ef65.eastus2.azurecontainerapps.io'

// ── Logic App (Consumption) ───────────────────────────────────────────────────
resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-cua-rpa-${resourceToken}'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    parameters: {}
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        cuaApiUrl: {
          defaultValue: cuaApiUrl
          type: 'String'
        }
        storageAccountName: {
          defaultValue: storageAccountName
          type: 'String'
        }
      }
      triggers: {
        When_a_HTTP_request_is_received: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {}
          }
        }
      }
      actions: {
        // ── A0: Event Grid subscription validation handshake ─────────────
        Check_If_Validation: {
          type: 'If'
          runAfter: {}
          expression: {
            and: [
              {
                equals: [
                  '@{triggerBody()?[0]?[\'eventType\']}'
                  'Microsoft.EventGrid.SubscriptionValidationEvent'
                ]
              }
            ]
          }
          actions: {
            Respond_Validation: {
              type: 'Response'
              runAfter: {}
              inputs: {
                statusCode: 200
                headers: {
                  'Content-Type': 'application/json'
                }
                body: {
                  validationResponse: '@{triggerBody()?[0]?[\'data\']?[\'validationCode\']}'
                }
              }
            }
            Terminate_After_Validation: {
              type: 'Terminate'
              runAfter: {
                Respond_Validation: ['Succeeded']
              }
              inputs: {
                runStatus: 'Succeeded'
              }
            }
          }
          else: {
            actions: {}
          }
        }

        // ── A: Get blob content via MSI-authenticated HTTP GET ────────────
        Get_Blob_Content: {
          type: 'Http'
          runAfter: {
            Check_If_Validation: ['Succeeded']
          }
          inputs: {
            method: 'GET'
            uri: '@{triggerBody()?[0]?[\'data\']?[\'url\']}'
            headers: {
              'x-ms-version': '2020-10-02'
            }
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: 'https://storage.azure.com/'
            }
          }
        }

        // ── B: Parse the JSON payload ────────────────────────────────────
        Parse_Payload_JSON: {
          type: 'ParseJson'
          runAfter: {
            Get_Blob_Content: ['Succeeded']
          }
          inputs: {
            content: '@body(\'Get_Blob_Content\')'
            schema: {
              type: 'object'
              properties: {
                // shared
                mode:              { type: 'string' }
                operation:         { type: 'string' }
                id:                { type: 'string' }
                name:              { type: 'string' }
                // create fields
                passenger_name:         { type: 'string' }
                passenger_email:        { type: 'string' }
                passenger_phone:        { type: 'string' }
                flight_number:          { type: 'string' }
                pnr:                    { type: 'string' }
                category:               { type: 'string' }
                subcategory:            { type: 'string' }
                severity:               { type: 'string' }
                agent:                  { type: 'string' }
                complaint_description:  { type: 'string' }
                description:            { type: 'string' }
                // update fields
                target_passenger_name:  { type: 'string' }
                target_flight_number:   { type: 'string' }
                target_pnr:             { type: 'string' }
                new_status:             { type: 'string' }
                new_severity:           { type: 'string' }
                new_agent:              { type: 'string' }
                new_score:              { type: 'string' }
                new_notes:              { type: 'string' }
              }
            }
          }
        }

        // ── C: Detect mode ───────────────────────────────────────────────
        Detect_Mode: {
          type: 'InitializeVariable'
          runAfter: {
            Parse_Payload_JSON: ['Succeeded']
          }
          inputs: {
            variables: [
              {
                name: 'varMode'
                type: 'string'
                value: '@{if(or(equals(toLower(coalesce(body(\'Parse_Payload_JSON\')?[\'mode\'],\'\')),\'create\'),equals(toLower(coalesce(body(\'Parse_Payload_JSON\')?[\'mode\'],\'\')),\'update\')),toLower(coalesce(body(\'Parse_Payload_JSON\')?[\'mode\'],\'\')),if(or(equals(toLower(coalesce(body(\'Parse_Payload_JSON\')?[\'operation\'],\'\')),\'create\'),equals(toLower(coalesce(body(\'Parse_Payload_JSON\')?[\'operation\'],\'\')),\'update\')),toLower(coalesce(body(\'Parse_Payload_JSON\')?[\'operation\'],\'\')),if(not(empty(coalesce(body(\'Parse_Payload_JSON\')?[\'target_passenger_name\'],\'\'))),\'update\',\'create\')))}'
              }
            ]
          }
        }

        Init_JobId: {
          type: 'InitializeVariable'
          runAfter: {
            Detect_Mode: ['Succeeded']
          }
          inputs: {
            variables: [
              {
                name: 'varJobId'
                type: 'string'
                value: ''
              }
            ]
          }
        }

        Init_JobStatus: {
          type: 'InitializeVariable'
          runAfter: {
            Init_JobId: ['Succeeded']
          }
          inputs: {
            variables: [
              {
                name: 'varJobStatus'
                type: 'string'
                value: 'queued'
              }
            ]
          }
        }

        Init_PollCount: {
          type: 'InitializeVariable'
          runAfter: {
            Init_JobStatus: ['Succeeded']
          }
          inputs: {
            variables: [
              {
                name: 'varPollCount'
                type: 'integer'
                value: 0
              }
            ]
          }
        }

        Init_Summary: {
          type: 'InitializeVariable'
          runAfter: {
            Init_PollCount: ['Succeeded']
          }
          inputs: {
            variables: [
              {
                name: 'varSummary'
                type: 'string'
                value: ''
              }
            ]
          }
        }

        // ── D: Mode-specific validation ──────────────────────────────────
        Validate_Payload: {
          type: 'If'
          runAfter: {
            Init_Summary: ['Succeeded']
          }
          expression: {
            and: [
              // mode is known
              {
                or: [
                  { equals: ['@variables(\'varMode\')', 'create'] }
                  { equals: ['@variables(\'varMode\')', 'update'] }
                ]
              }
            ]
          }
          actions: {
            // ── D1: CREATE validation branch ─────────────────────────────
            Validate_Create_Or_Update: {
              type: 'If'
              runAfter: {}
              expression: {
                and: [
                  { equals: ['@variables(\'varMode\')', 'create'] }
                ]
              }
              actions: {
                // All required create fields must be non-empty
                Check_Create_Required_Fields: {
                  type: 'If'
                  runAfter: {}
                  expression: {
                    and: [
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'passenger_name\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'passenger_email\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'passenger_phone\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'flight_number\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'pnr\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'category\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'subcategory\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'severity\'],\'\')', ''] } }
                      { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'agent\'],\'\')', ''] } }
                      {
                        not: {
                          and: [
                            { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'complaint_description\'],\'\')', ''] }
                            { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'description\'],\'\')', ''] }
                          ]
                        }
                      }
                      // severity enum
                      {
                        or: [
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'severity\']', 'Low'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'severity\']', 'Medium'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'severity\']', 'High'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'severity\']', 'Critical'] }
                        ]
                      }
                      // category enum
                      {
                        or: [
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'category\']', 'Baggage'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'category\']', 'Flight Operations'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'category\']', 'Booking & Refunds'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'category\']', 'Safety'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'category\']', 'Seating'] }
                          { equals: ['@body(\'Parse_Payload_JSON\')?[\'category\']', 'Special Assistance'] }
                        ]
                      }
                    ]
                  }
                  // ── VALID CREATE: proceed to API call ─────────────────
                  actions: {
                    Post_To_CUA_API_Create: {
                      type: 'Http'
                      runAfter: {}
                      inputs: {
                        method: 'POST'
                        uri: '@{parameters(\'cuaApiUrl\')}/api/run'
                        headers: {
                          'Content-Type': 'application/json'
                        }
                        body: '@body(\'Parse_Payload_JSON\')'
                        retryPolicy: {
                          type: 'fixed'
                          count: 2
                          interval: 'PT10S'
                        }
                      }
                    }
                    Parse_Run_Response_Create: {
                      type: 'ParseJson'
                      runAfter: {
                        Post_To_CUA_API_Create: ['Succeeded']
                      }
                      inputs: {
                        content: '@body(\'Post_To_CUA_API_Create\')'
                        schema: {
                          type: 'object'
                          properties: {
                            job_id:   { type: 'string' }
                            status:   { type: 'string' }
                            poll_url: { type: 'string' }
                          }
                        }
                      }
                    }
                    Set_JobId_Create: {
                      type: 'SetVariable'
                      runAfter: {
                        Parse_Run_Response_Create: ['Succeeded']
                      }
                      inputs: {
                        name: 'varJobId'
                        value: '@body(\'Parse_Run_Response_Create\')?[\'job_id\']'
                      }
                    }
                    Set_JobStatus_Create: {
                      type: 'SetVariable'
                      runAfter: {
                        Set_JobId_Create: ['Succeeded']
                      }
                      inputs: {
                        name: 'varJobStatus'
                        value: '@coalesce(body(\'Parse_Run_Response_Create\')?[\'status\'],\'queued\')'
                      }
                    }
                    // Poll until done
                    Poll_Until_Done_Create: {
                      type: 'Until'
                      runAfter: {
                        Set_JobStatus_Create: ['Succeeded']
                      }
                      expression: '@or(or(equals(variables(\'varJobStatus\'),\'completed\'),equals(variables(\'varJobStatus\'),\'failed\')),greaterOrEquals(variables(\'varPollCount\'),20))'
                      limit: {
                        count: 20
                        timeout: 'PT10M'
                      }
                      actions: {
                        Delay_15s_Create: {
                          type: 'Wait'
                          runAfter: {}
                          inputs: {
                            interval: {
                              count: 15
                              unit: 'Second'
                            }
                          }
                        }
                        Get_Job_Status_Create: {
                          type: 'Http'
                          runAfter: {
                            Delay_15s_Create: ['Succeeded']
                          }
                          inputs: {
                            method: 'GET'
                            uri: '@{parameters(\'cuaApiUrl\')}/api/status/@{variables(\'varJobId\')}'
                          }
                        }
                        Parse_Status_Response_Create: {
                          type: 'ParseJson'
                          runAfter: {
                            Get_Job_Status_Create: ['Succeeded']
                          }
                          inputs: {
                            content: '@body(\'Get_Job_Status_Create\')'
                            schema: {
                              type: 'object'
                              properties: {
                                status:  { type: 'string' }
                                summary: { type: 'string' }
                              }
                            }
                          }
                        }
                        Update_JobStatus_Create: {
                          type: 'SetVariable'
                          runAfter: {
                            Parse_Status_Response_Create: ['Succeeded']
                          }
                          inputs: {
                            name: 'varJobStatus'
                            value: '@coalesce(body(\'Parse_Status_Response_Create\')?[\'status\'],\'running\')'
                          }
                        }
                        Update_Summary_Create: {
                          type: 'SetVariable'
                          runAfter: {
                            Update_JobStatus_Create: ['Succeeded']
                          }
                          inputs: {
                            name: 'varSummary'
                            value: '@{coalesce(body(\'Parse_Status_Response_Create\')?[\'summary\'],\'\')}'
                          }
                        }
                        Increment_PollCount_Create: {
                          type: 'IncrementVariable'
                          runAfter: {
                            Update_Summary_Create: ['Succeeded']
                          }
                          inputs: {
                            name: 'varPollCount'
                            value: 1
                          }
                        }
                      }
                    }
                    // Write audit record for valid create
                    Write_Audit_Create: {
                      type: 'Http'
                      runAfter: {
                        Poll_Until_Done_Create: ['Succeeded', 'Failed', 'TimedOut']
                      }
                      inputs: {
                        method: 'POST'
                        uri: 'https://@{parameters(\'storageAccountName\')}.table.core.windows.net/logicAppAudit'
                        headers: {
                          'Content-Type': 'application/json'
                          Accept: 'application/json;odata=nometadata'
                          'x-ms-version': '2021-12-02'
                          'x-ms-date': '@{utcNow(\'R\')}'
                        }
                        authentication: {
                          type: 'ManagedServiceIdentity'
                          audience: 'https://storage.azure.com/'
                        }
                        body: {
                          PartitionKey: '@{formatDateTime(utcNow(),\'yyyy-MM-dd\')}'
                          RowKey: '@{if(empty(variables(\'varJobId\')),guid(),variables(\'varJobId\'))}'
                          BlobPath: '@{triggerBody()?[0]?[\'subject\']}'
                          Mode: 'create'
                          FinalStatus: '@{variables(\'varJobStatus\')}'
                          Summary: '@{variables(\'varSummary\')}'
                          PollCount: '@{variables(\'varPollCount\')}'
                          CompletedAt: '@{utcNow()}'
                          PassengerName: '@{coalesce(body(\'Parse_Payload_JSON\')?[\'passenger_name\'],\'\')}'
                          FlightNumber: '@{coalesce(body(\'Parse_Payload_JSON\')?[\'flight_number\'],\'\')}'
                          Pnr: '@{coalesce(body(\'Parse_Payload_JSON\')?[\'pnr\'],\'\')}'
                        }
                      }
                    }
                  }
                  // ── INVALID CREATE: reject ────────────────────────────
                  else: {
                    actions: {
                      Write_Audit_Rejected_Create: {
                        type: 'Http'
                        runAfter: {}
                        inputs: {
                          method: 'POST'
                          uri: 'https://@{parameters(\'storageAccountName\')}.table.core.windows.net/logicAppAudit'
                          headers: {
                            'Content-Type': 'application/json'
                            Accept: 'application/json;odata=nometadata'
                            'x-ms-version': '2021-12-02'
                            'x-ms-date': '@{utcNow(\'R\')}'
                          }
                          authentication: {
                            type: 'ManagedServiceIdentity'
                            audience: 'https://storage.azure.com/'
                          }
                          body: {
                            PartitionKey: '@{formatDateTime(utcNow(),\'yyyy-MM-dd\')}'
                            RowKey: '@{guid()}'
                            BlobPath: '@{triggerBody()?[0]?[\'subject\']}'
                            Mode: 'create'
                            FinalStatus: 'rejected'
                            Summary: 'Payload failed create validation: required fields missing or enum value invalid (severity/category).'
                            PollCount: 0
                            CompletedAt: '@{utcNow()}'
                          }
                        }
                      }
                      Terminate_Rejected_Create: {
                        type: 'Terminate'
                        runAfter: {
                          Write_Audit_Rejected_Create: ['Succeeded', 'Failed']
                        }
                        inputs: {
                          runStatus: 'Failed'
                          runError: {
                            code: 'VALIDATION_FAILED'
                            message: 'Create payload rejected: required fields missing or enum value out of range.'
                          }
                        }
                      }
                    }
                  }
                }
              }
              // ── D2: UPDATE validation branch ──────────────────────────
              else: {
                actions: {
                  Check_Update_Required_Fields: {
                    type: 'If'
                    runAfter: {}
                    expression: {
                      and: [
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'target_passenger_name\'],\'\')', ''] } }
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'target_flight_number\'],\'\')', ''] } }
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'target_pnr\'],\'\')', ''] } }
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'new_status\'],\'\')', ''] } }
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'new_severity\'],\'\')', ''] } }
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'new_agent\'],\'\')', ''] } }
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'new_score\'],\'\')', ''] } }
                        { not: { equals: ['@coalesce(body(\'Parse_Payload_JSON\')?[\'new_notes\'],\'\')', ''] } }
                        // new_status enum
                        {
                          or: [
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_status\']', 'Open'] }
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_status\']', 'Under Review'] }
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_status\']', 'Resolved'] }
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_status\']', 'Closed'] }
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_status\']', 'Escalated'] }
                          ]
                        }
                        // new_severity enum
                        {
                          or: [
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_severity\']', 'Low'] }
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_severity\']', 'Medium'] }
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_severity\']', 'High'] }
                            { equals: ['@body(\'Parse_Payload_JSON\')?[\'new_severity\']', 'Critical'] }
                          ]
                        }
                      ]
                    }
                    // ── VALID UPDATE: proceed to API call ─────────────────
                    actions: {
                      Post_To_CUA_API_Update: {
                        type: 'Http'
                        runAfter: {}
                        inputs: {
                          method: 'POST'
                          uri: '@{parameters(\'cuaApiUrl\')}/api/run'
                          headers: {
                            'Content-Type': 'application/json'
                          }
                          body: '@body(\'Parse_Payload_JSON\')'
                          retryPolicy: {
                            type: 'fixed'
                            count: 2
                            interval: 'PT10S'
                          }
                        }
                      }
                      Parse_Run_Response_Update: {
                        type: 'ParseJson'
                        runAfter: {
                          Post_To_CUA_API_Update: ['Succeeded']
                        }
                        inputs: {
                          content: '@body(\'Post_To_CUA_API_Update\')'
                          schema: {
                            type: 'object'
                            properties: {
                              job_id:   { type: 'string' }
                              status:   { type: 'string' }
                              poll_url: { type: 'string' }
                            }
                          }
                        }
                      }
                      Set_JobId_Update: {
                        type: 'SetVariable'
                        runAfter: {
                          Parse_Run_Response_Update: ['Succeeded']
                        }
                        inputs: {
                          name: 'varJobId'
                          value: '@body(\'Parse_Run_Response_Update\')?[\'job_id\']'
                        }
                      }
                      Set_JobStatus_Update: {
                        type: 'SetVariable'
                        runAfter: {
                          Set_JobId_Update: ['Succeeded']
                        }
                        inputs: {
                          name: 'varJobStatus'
                          value: '@coalesce(body(\'Parse_Run_Response_Update\')?[\'status\'],\'queued\')'
                        }
                      }
                      Poll_Until_Done_Update: {
                        type: 'Until'
                        runAfter: {
                          Set_JobStatus_Update: ['Succeeded']
                        }
                        expression: '@or(or(equals(variables(\'varJobStatus\'),\'completed\'),equals(variables(\'varJobStatus\'),\'failed\')),greaterOrEquals(variables(\'varPollCount\'),20))'
                        limit: {
                          count: 20
                          timeout: 'PT10M'
                        }
                        actions: {
                          Delay_15s_Update: {
                            type: 'Wait'
                            runAfter: {}
                            inputs: {
                              interval: {
                                count: 15
                                unit: 'Second'
                              }
                            }
                          }
                          Get_Job_Status_Update: {
                            type: 'Http'
                            runAfter: {
                              Delay_15s_Update: ['Succeeded']
                            }
                            inputs: {
                              method: 'GET'
                              uri: '@{parameters(\'cuaApiUrl\')}/api/status/@{variables(\'varJobId\')}'
                            }
                          }
                          Parse_Status_Response_Update: {
                            type: 'ParseJson'
                            runAfter: {
                              Get_Job_Status_Update: ['Succeeded']
                            }
                            inputs: {
                              content: '@body(\'Get_Job_Status_Update\')'
                              schema: {
                                type: 'object'
                                properties: {
                                  status:  { type: 'string' }
                                  summary: { type: 'string' }
                                }
                              }
                            }
                          }
                          Update_JobStatus_Update: {
                            type: 'SetVariable'
                            runAfter: {
                              Parse_Status_Response_Update: ['Succeeded']
                            }
                            inputs: {
                              name: 'varJobStatus'
                              value: '@coalesce(body(\'Parse_Status_Response_Update\')?[\'status\'],\'running\')'
                            }
                          }
                          Update_Summary_Update: {
                            type: 'SetVariable'
                            runAfter: {
                              Update_JobStatus_Update: ['Succeeded']
                            }
                            inputs: {
                              name: 'varSummary'
                              value: '@{coalesce(body(\'Parse_Status_Response_Update\')?[\'summary\'],\'\')}'
                            }
                          }
                          Increment_PollCount_Update: {
                            type: 'IncrementVariable'
                            runAfter: {
                              Update_Summary_Update: ['Succeeded']
                            }
                            inputs: {
                              name: 'varPollCount'
                              value: 1
                            }
                          }
                        }
                      }
                      Write_Audit_Update: {
                        type: 'Http'
                        runAfter: {
                          Poll_Until_Done_Update: ['Succeeded', 'Failed', 'TimedOut']
                        }
                        inputs: {
                          method: 'POST'
                          uri: 'https://@{parameters(\'storageAccountName\')}.table.core.windows.net/logicAppAudit'
                          headers: {
                            'Content-Type': 'application/json'
                            Accept: 'application/json;odata=nometadata'
                            'x-ms-version': '2021-12-02'
                            'x-ms-date': '@{utcNow(\'R\')}'
                          }
                          authentication: {
                            type: 'ManagedServiceIdentity'
                            audience: 'https://storage.azure.com/'
                          }
                          body: {
                            PartitionKey: '@{formatDateTime(utcNow(),\'yyyy-MM-dd\')}'
                            RowKey: '@{if(empty(variables(\'varJobId\')),guid(),variables(\'varJobId\'))}'
                            BlobPath: '@{triggerBody()?[0]?[\'subject\']}'
                            Mode: 'update'
                            FinalStatus: '@{variables(\'varJobStatus\')}'
                            Summary: '@{variables(\'varSummary\')}'
                            PollCount: '@{variables(\'varPollCount\')}'
                            CompletedAt: '@{utcNow()}'
                            TargetPassengerName: '@{coalesce(body(\'Parse_Payload_JSON\')?[\'target_passenger_name\'],\'\')}'
                            TargetFlightNumber: '@{coalesce(body(\'Parse_Payload_JSON\')?[\'target_flight_number\'],\'\')}'
                            TargetPnr: '@{coalesce(body(\'Parse_Payload_JSON\')?[\'target_pnr\'],\'\')}'
                            NewStatus: '@{coalesce(body(\'Parse_Payload_JSON\')?[\'new_status\'],\'\')}'
                          }
                        }
                      }
                    }
                    // ── INVALID UPDATE: reject ────────────────────────────
                    else: {
                      actions: {
                        Write_Audit_Rejected_Update: {
                          type: 'Http'
                          runAfter: {}
                          inputs: {
                            method: 'POST'
                            uri: 'https://@{parameters(\'storageAccountName\')}.table.core.windows.net/logicAppAudit'
                            headers: {
                              'Content-Type': 'application/json'
                              Accept: 'application/json;odata=nometadata'
                              'x-ms-version': '2021-12-02'
                              'x-ms-date': '@{utcNow(\'R\')}'
                            }
                            authentication: {
                              type: 'ManagedServiceIdentity'
                              audience: 'https://storage.azure.com/'
                            }
                            body: {
                              PartitionKey: '@{formatDateTime(utcNow(),\'yyyy-MM-dd\')}'
                              RowKey: '@{guid()}'
                              BlobPath: '@{triggerBody()?[0]?[\'subject\']}'
                              Mode: 'update'
                              FinalStatus: 'rejected'
                              Summary: 'Payload failed update validation: required fields missing or enum value invalid (new_status/new_severity).'
                              PollCount: 0
                              CompletedAt: '@{utcNow()}'
                            }
                          }
                        }
                        Terminate_Rejected_Update: {
                          type: 'Terminate'
                          runAfter: {
                            Write_Audit_Rejected_Update: ['Succeeded', 'Failed']
                          }
                          inputs: {
                            runStatus: 'Failed'
                            runError: {
                              code: 'VALIDATION_FAILED'
                              message: 'Update payload rejected: required fields missing or enum value out of range.'
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
          // ── Unknown mode: reject ──────────────────────────────────────
          else: {
            actions: {
              Write_Audit_Unknown_Mode: {
                type: 'Http'
                runAfter: {}
                inputs: {
                  method: 'POST'
                  uri: 'https://@{parameters(\'storageAccountName\')}.table.core.windows.net/logicAppAudit'
                  headers: {
                    'Content-Type': 'application/json'
                    Accept: 'application/json;odata=nometadata'
                    'x-ms-version': '2021-12-02'
                    'x-ms-date': '@{utcNow(\'R\')}'
                  }
                  authentication: {
                    type: 'ManagedServiceIdentity'
                    audience: 'https://storage.azure.com/'
                  }
                  body: {
                    PartitionKey: '@{formatDateTime(utcNow(),\'yyyy-MM-dd\')}'
                    RowKey: '@{guid()}'
                    BlobPath: '@{triggerBody()?[0]?[\'subject\']}'
                    Mode: '@{variables(\'varMode\')}'
                    FinalStatus: 'rejected'
                    Summary: 'Could not determine mode (create/update) from payload. Neither mode/operation field is valid nor update-specific keys are present.'
                    PollCount: 0
                    CompletedAt: '@{utcNow()}'
                  }
                }
              }
              Terminate_Unknown_Mode: {
                type: 'Terminate'
                runAfter: {
                  Write_Audit_Unknown_Mode: ['Succeeded', 'Failed']
                }
                inputs: {
                  runStatus: 'Failed'
                  runError: {
                    code: 'UNKNOWN_MODE'
                    message: 'Could not determine payload mode (create/update). Check mode/operation field.'
                  }
                }
              }
            }
          }
        }
      }
      outputs: {}
    }
  }
}

// ── Outputs ────────────────────────────────────────────────────────────────
output logicAppId string = logicApp.id
output logicAppName string = logicApp.name
output logicAppPrincipalId string = logicApp.identity.principalId
output triggerCallbackUrl string = listCallbackUrl('${logicApp.id}/triggers/When_a_HTTP_request_is_received', '2019-05-01').value
