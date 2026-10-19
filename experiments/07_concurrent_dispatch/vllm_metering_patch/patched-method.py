async def chat_completion_full_generator(
    self,
    request: ChatCompletionRequest,
    result_generator: AsyncIterator[RequestOutput],
    request_id: str,
    model_name: str,
    conversation: list[ConversationMessage],
    tokenizer: AnyTokenizer,
    request_metadata: RequestResponseMetadata,
) -> ErrorResponse | ChatCompletionResponse:
    created_time = int(time.time())
    final_res: RequestOutput | None = None

    try:
        async for res in result_generator:
            final_res = res
    except asyncio.CancelledError:
        return self.create_error_response("Client disconnected")
    except ValueError as e:
        # TODO: Use a vllm-specific Validation Error
        return self.create_error_response(str(e))

    assert final_res is not None

    choices: list[ChatCompletionResponseChoice] = []
    if self.tool_call_id_type == "kimi_k2":
        history_tool_call_cnt = get_history_tool_calls_cnt(conversation)
    else:
        history_tool_call_cnt = 0

    role = self.get_chat_request_role(request)
    for output in final_res.outputs:
        token_ids = output.token_ids
        out_logprobs = output.logprobs
        tool_call_info = None

        if request.logprobs and request.top_logprobs is not None:
            assert out_logprobs is not None, "Did not output logprobs"
            logprobs = self._create_chat_logprobs(
                token_ids=token_ids,
                top_logprobs=out_logprobs,
                num_output_top_logprobs=request.top_logprobs,
                tokenizer=tokenizer,
                return_as_token_id=request.return_tokens_as_token_ids,
            )
        else:
            logprobs = None

        if self.use_harmony:
            reasoning, content, _ = parse_chat_output(token_ids)
            if not request.include_reasoning:
                reasoning = None

            if self.tool_parser is not None:
                tool_parser = self.tool_parser(tokenizer)
                # NOTE: We use token_ids for openai tool parser
                tool_call_info = tool_parser.extract_tool_calls(
                    "",
                    request=request,
                    token_ids=token_ids,  # type: ignore
                )
                content = tool_call_info.content
                message = ChatMessage(
                    role=role,
                    reasoning=reasoning,
                    content=content,
                    tool_calls=tool_call_info.tool_calls,
                )
            else:
                message = ChatMessage(
                    role=role,
                    reasoning=reasoning,
                    content=content,
                )

            choice_data = ChatCompletionResponseChoice(
                index=output.index,
                message=message,
                logprobs=logprobs,
                finish_reason=(
                    "tool_calls"
                    if (tool_call_info is not None and tool_call_info.tools_called)
                    else output.finish_reason
                    if output.finish_reason
                    else "stop"
                ),
                stop_reason=output.stop_reason,
                token_ids=(
                    as_list(output.token_ids) if request.return_token_ids else None
                ),
            )
            choices.append(choice_data)
            continue

        if self.reasoning_parser:
            try:
                reasoning_parser = self.reasoning_parser(
                    tokenizer,
                    chat_template_kwargs=request.chat_template_kwargs,  # type: ignore
                )
            except RuntimeError as e:
                logger.exception("Error in reasoning parser creation.")
                return self.create_error_response(str(e))
            # If the reasoning parser is enabled,
            # tool calls are extracted exclusively from the content.
            reasoning, content = reasoning_parser.extract_reasoning(
                output.text, request=request
            )
            if not request.include_reasoning:
                reasoning = None
        else:
            reasoning = None
            content = output.text

        auto_tools_called = False
        # if auto tools are not enabled, and a named tool choice using
        #   outlines is not being used
        tool_calls, content = self._parse_tool_calls_from_content(
            request=request,
            tokenizer=tokenizer,
            content=content,
            enable_auto_tools=self.enable_auto_tools,
            tool_parser_cls=self.tool_parser,
        )
        tool_call_class = (
            MistralToolCall if isinstance(tokenizer, MistralTokenizer) else ToolCall
        )
        if (not self.enable_auto_tools or not self.tool_parser) and (
            not isinstance(request.tool_choice, ChatCompletionNamedToolChoiceParam)
            and request.tool_choice != "required"
        ):
            message = ChatMessage(role=role, reasoning=reasoning, content=content)

        # if the request uses tools and specified a tool choice
        elif (
            request.tool_choice
            and type(request.tool_choice) is ChatCompletionNamedToolChoiceParam
        ):
            assert tool_calls is not None and len(tool_calls) > 0
            message = ChatMessage(
                role=role,
                reasoning=reasoning,
                content="",
                tool_calls=[tool_call_class(function=tc) for tc in tool_calls],
            )

        elif request.tool_choice and request.tool_choice == "required":
            tool_call_class_items = []
            assert tool_calls is not None and len(tool_calls) > 0
            for tool_call in tool_calls:
                tool_call_class_items.append(
                    tool_call_class(
                        id=make_tool_call_id(
                            id_type=self.tool_call_id_type,
                            func_name=tool_call.name,
                            idx=history_tool_call_cnt,
                        ),
                        function=tool_call,
                    )
                )
                history_tool_call_cnt += 1
            message = ChatMessage(
                role=role,
                content="",
                tool_calls=tool_call_class_items,
                reasoning=reasoning,
            )

        # if the request doesn't use tool choice
        # OR specifies to not use a tool
        elif not request.tool_choice or request.tool_choice == "none":
            message = ChatMessage(role=role, reasoning=reasoning, content=content)

        # handle when there are tools and tool choice is auto
        elif (
            request.tools
            and (request.tool_choice == "auto" or request.tool_choice is None)
            and self.enable_auto_tools
            and self.tool_parser
        ):
            # In the OpenAI API the finish_reason is "tools_called"
            # if the tool choice is auto and the model produced a tool
            # call. The same is not true for named function calls
            auto_tools_called = tool_calls is not None and len(tool_calls) > 0
            if tool_calls:
                message = ChatMessage(
                    role=role,
                    reasoning=reasoning,
                    content=content,
                    tool_calls=[
                        ToolCall(
                            function=tc,
                            type="function",
                        )
                        for tc in tool_calls
                    ],
                )

            else:
                # FOR NOW make it a chat message; we will have to detect
                # the type to make it later.
                ret_content = content

                # try to use content return from tool parser first,
                # tool parser may do some modify for the content.
                if content and len(content) > 0:
                    ret_content = content
                message = ChatMessage(
                    role=role,
                    reasoning=reasoning,
                    content=ret_content,
                )

        # undetermined case that is still important to handle
        else:
            logger.error(
                "Error in chat_completion_full_generator - cannot determine"
                " if tools should be extracted. Returning a standard chat "
                "completion."
            )
            message = ChatMessage(role=role, reasoning=reasoning, content=content)
        # In OpenAI's API, when a tool is called, the finish_reason is:
        # "tool_calls" for "auto" or "required" tool calls,
        # and "stop" for named tool calls.
        is_finish_reason_tool_calls = auto_tools_called or (
            request.tool_choice
            and request.tool_choice == "required"
            and output.finish_reason == "stop"
        )

        choice_data = ChatCompletionResponseChoice(
            index=output.index,
            message=message,
            logprobs=logprobs,
            finish_reason="tool_calls"
            if is_finish_reason_tool_calls
            else output.finish_reason
            if output.finish_reason
            else "stop",
            stop_reason=output.stop_reason,
            token_ids=(
                as_list(output.token_ids) if request.return_token_ids else None
            ),
        )

        choices.append(choice_data)

    if request.echo:
        last_msg_content: str | list[dict[str, str]] = ""
        if (
            conversation
            and "content" in conversation[-1]
            and conversation[-1].get("role") == role
        ):
            last_msg_content = conversation[-1]["content"] or ""
        if isinstance(last_msg_content, list):
            last_msg_content = "\n".join(msg["text"] for msg in last_msg_content)

        for choice in choices:
            full_message = last_msg_content + (choice.message.content or "")
            choice.message.content = full_message

    assert final_res.prompt_token_ids is not None
    num_prompt_tokens = len(final_res.prompt_token_ids)
    if final_res.encoder_prompt_token_ids is not None:
        num_prompt_tokens += len(final_res.encoder_prompt_token_ids)
    num_generated_tokens = sum(
        len(output.token_ids) for output in final_res.outputs
    )
    usage = UsageInfo(
        prompt_tokens=num_prompt_tokens,
        completion_tokens=num_generated_tokens,
        total_tokens=num_prompt_tokens + num_generated_tokens,
    )
    if self.enable_prompt_tokens_details and final_res.num_cached_tokens is not None:
        usage.prompt_tokens_details = PromptTokenUsageInfo(
            cached_tokens=final_res.num_cached_tokens
        )

    request_metadata.final_usage_info = usage

    response = ChatCompletionResponse(
        id=request_id,
        created=created_time,
        model=model_name,
        choices=choices,
        usage=usage,
        prompt_logprobs=clamp_prompt_logprobs(final_res.prompt_logprobs),
        prompt_token_ids=(
            final_res.prompt_token_ids if request.return_token_ids else None
        ),
        kv_transfer_params=final_res.kv_transfer_params,
    )

    # Log complete response if output logging is enabled
    if self.enable_log_outputs and self.request_logger:
        for choice in choices:
            output_text = ""
            if choice.message.content:
                output_text = choice.message.content
            elif choice.message.tool_calls:
                # For tool calls, log the function name and arguments
                tool_call_descriptions = []
                for tc in choice.message.tool_calls:
                    if hasattr(tc.function, "name") and hasattr(
                        tc.function, "arguments"
                    ):
                        tool_call_descriptions.append(
                            f"{tc.function.name}({tc.function.arguments})"
                        )
                tool_calls_str = ", ".join(tool_call_descriptions)
                output_text = f"[tool_calls: {tool_calls_str}]"

            if output_text:
                # Get the corresponding output token IDs
                output_token_ids = None
                if choice.index < len(final_res.outputs):
                    output_token_ids = final_res.outputs[choice.index].token_ids

                self.request_logger.log_outputs(
                    request_id=request_id,
                    outputs=output_text,
                    output_token_ids=output_token_ids,
                    finish_reason=choice.finish_reason,
                    is_streaming=False,
                    delta=False,
                )

    return response
