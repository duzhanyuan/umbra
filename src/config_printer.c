#include <stdlib.h>
#include <stdio.h>
#include "shim.h"

#include "config_header.h"

#define TAB_SIZE 4

#define print_str_macro(macro) printf("%s = \"%s\"\n", (#macro), (macro))
#define print_int_macro(macro) printf("%s = \"%d\"\n", (#macro), (macro))
#define print_bool_macro(macro) printf("%s = \"%s\"\n", (#macro), (macro) ? "true" : "false")

#define print_str_arr(var) { printf("%s = {\n", #var); \
    int i; \
    for (i = 0; i < sizeof(var) / sizeof(*var); i++) { \
        printf("    \"%s\",\n", var[i]); \
    } \
    printf("}\n"); \
    }

void print_indent(int depth) {
    int i;
    for (i = 0; i < depth * TAB_SIZE; i++) {
        printf(" ");
    }
}

#define print_http_req_part(REQ) \
        if (r & HTTP_REQ_##REQ) { \
            printf(#REQ "|"); \
        }

void print_http_req_field(int r) {
    printf("|");
    print_http_req_part(HEAD);
    print_http_req_part(GET);
    print_http_req_part(POST);
    print_http_req_part(PUT);
    print_http_req_part(DELETE);
    print_http_req_part(TRACE);
    print_http_req_part(CONNECT);
}

#define printf_indent(depth, args...) print_indent(depth); printf(args)

#define print_str_field(item) printf_indent(depth + 1, "." #item " = \"%s\"\n", \
        p->item)
#define print_int_field(item) printf_indent(depth + 1, "." #item " = %d\n", \
        p->item)
#define print_http_req_field(item) printf_indent(depth + 1, "." #item " = "); \
        print_http_req_field(p->item); printf("\n")
#define print_bool_field(item) printf_indent(depth + 1, "." #item " = %s\n", \
        p->item ? "TRUE" : "FALSE")

void print_params(struct params *p, int depth) {
    printf_indent(depth, "\"%s\" {\n", p->name);
    print_str_field(whitelist);
    print_int_field(max_param_len);
    printf_indent(depth, "}\n");
}

void print_page_conf(struct page_conf *p, int depth) {
    int i;

    printf_indent(depth, "\"%s\" {\n", p->name);

    print_str_field(whitelist);
    print_int_field(max_param_len);
    print_int_field(max_request_payload_len);
    print_bool_field(params_allowed);
    print_http_req_field(request_types);
    print_bool_field(requires_login);
    print_int_field(params_len);

    printf_indent(depth + 1, ".params = {\n");
    for (i = 0; i < p->params_len; i++) {
        print_params(&p->params[i], depth + 2);
    }
    printf_indent(depth + 1, "},\n");

    printf_indent(depth, "},\n");
}

int main(int argc, char **argv) {
    int i, j;
    init_config_vars();

    printf("** Global Config **\n");
    print_str_macro(HTTPS_PRIVATE_KEY);
    print_str_macro(HTTPS_CERTIFICATE);
    print_int_macro(MAX_HEADER_FIELD_LEN);
    print_str_arr(successful_login_pages);
    print_int_macro(MAX_HEADER_VALUE_LEN);

    printf("\n** Global Page Defaults **\n");
    print_page_conf(&default_page_conf, 0);

    puts("");

    printf("** Page-specific Config**\n");
    for (i = 0; i < PAGES_CONF_LEN; i++) {
        print_page_conf(&pages_conf[i], 0);
    }
    printf("}\n");

    return 0;
}