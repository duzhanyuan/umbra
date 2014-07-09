#ifndef SHIM_H
#define SHIM_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netdb.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/epoll.h>
#include <errno.h>
#include <stdbool.h>
#include "http_parser.h"
#include "bytearray.h"

#define MAXEVENTS 256
#define READ_BUF_SIZE 4096

#define TRACE
#ifdef TRACE
#define log_trace(args...) fprintf(stdout, "[trace] " args); fflush(stdout)
#else
#define log_trace(msg, args...) ;
#endif

//#define DEBUG
#ifdef DEBUG
#define log_dbg(args...) fprintf(stdout, "[ dbg ] " args); fflush(stdout)
#else
#define log_dbg(msg, args...) ;
#endif

#define log_warn(args...) fprintf(stderr, "[warn ] " args); fflush(stdout)
#define log_info(args...) fprintf(stderr, "[info ] " args); fflush(stdout)
#define log_error(args...) fprintf(stderr, "[error] " args); fflush(stdout)

typedef enum {
    CLIENT_LISTENER, SERVER_LISTENER
} event_t;

typedef enum {
    WAITING_FOR_URL, WAITING_FOR_HEADER, WAITING_FOR_BODY, MESSAGE_COMPLETE
} conn_state_t;

#define HTTP_REQ_HEAD (1 << 0)
#define HTTP_REQ_GET (1 << 1)
#define HTTP_REQ_POST (1 << 2)
#define HTTP_REQ_PUT (1 << 3)
#define HTTP_REQ_DELETE (1 << 4)
#define HTTP_REQ_TRACE (1 << 5)
#define HTTP_REQ_CONNECT (1 << 6)

struct connection_info;

struct event_data {
    int listen_fd;
    int send_fd;
    http_parser parser;
    struct connection_info *conn_info;
    event_t type : 8;
    conn_state_t state : 8;
    bool is_cancelled : 1;
};

struct connection_info {
    struct event_data *client_ev_data;
    struct event_data *server_ev_data;
};

int make_socket_non_blocking(int sfd);
int create_and_bind(char *port);
int create_and_connect(char *port);
void free_connection_info(struct connection_info *ci);
int sendall(int sockfd, const void *buf, size_t len);

void handle_event(int efd, struct epoll_event *ev, int sfd);
int handle_client_event(struct epoll_event *ev);
int handle_server_event(struct epoll_event *ev);
void handle_new_connection(int efd, struct epoll_event *ev, int sfd);
void init_structures(char *error_page_file);
struct connection_info *init_conn_info(int infd, int outfd);

/* HTTP parser callbacks */
int on_message_begin_cb(http_parser *p);
int on_headers_complete_cb(http_parser *p);
int on_message_complete_cb(http_parser *p);
int on_url_cb(http_parser *p, const char *at, size_t length);
int on_header_field_cb(http_parser *p, const char *at, size_t length);
int on_header_value_cb(http_parser *p, const char *at, size_t length);
int on_body_cb(http_parser *p, const char *at, size_t length);

#define SIMPLE_HTTP_RESPONSE \
    "HTTP/1.0 201 OK\r\n" \
    "Content-type: text/html\r\n" \
    "\r\n"

#define DEFAULT_ERROR_PAGE_STR \
    "<html>" \
    "<head>" \
    "<title>Action Not Allowed</title>" \
    "</head>" \
    "<body>" \
    "<h1>Action Not Allowed</h1>" \
    "This request has been blocked by the firewall shim. " \
    "Please contact your network administrator for more details." \
    "</body>" \
    "</html>"

#endif
