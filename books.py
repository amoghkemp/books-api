import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.parse
import urllib.error
import psycopg
import re
import os
from dotenv import load_dotenv

load_dotenv()

books_db = os.getenv("books_db")
big_book_api_key = os.getenv("big_book_api_key")
big_book_url = os.getenv("big_book_url")

class bookRequestsHandler(BaseHTTPRequestHandler):

    def send_json(self, data, status_code = 200):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        # 1. GET /books
        if path == '/books':
            try:
                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        query = "Select id, title, author, genre FROM books ORDER by id ASC;"
                        cur.execute(query)
                        rows = cur.fetchall()

                        all_books = []
                        for row in rows:
                            all_books.append({
                                "id": row[0],
                                "title": row[1],
                                "author": row[2],
                                "genre": row[3]
                            })
                        self.send_json(all_books)
                        return
            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal Server Error"}, status_code=500)
                return

        # 2. GET /recommend
        elif path == '/recommend':
            genre_list = query_params.get('genre')

            if not genre_list:
                self.send_json({"detail": "Missing required query parameter: genre"}, status_code = 400)
                return
            requested_genre = genre_list[0]

            try:
                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        query = "SELECT id, title, author, genre FROM books WHERE LOWER(genre) = LOWER(%s);"
                        cur.execute(query, (requested_genre,))
                        rows = cur.fetchall()

                        recommendations = []
                        for row in rows:
                            recommendations.append({
                                "id": row[0],
                                "title": row[1],
                                "author": row[2],
                                "genre": row[3]
                            })
                        self.send_json(recommendations)
                        return
            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal Server Error"}, status_code = 500)
                return
        
        # 3. GET /search-external
        elif path == '/search-external':
            query_list = query_params.get('query')
            if not query_list:
                self.send_json({"detail": "Missing required query parameter: query"}, status_code = 400)
                return
            user_query = query_list[0]

            try:
                encoded_query = urllib.parse.quote(user_query)
                external_url = f"{big_book_url}?query={encoded_query}&api-key={big_book_api_key}"

                print(f"Fetching from Big book API: {external_url}")

                # create a fake browser request
                req = urllib.request.Request(
                    external_url,
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json'
                    }
                )

                with urllib.request.urlopen(req) as response:
                    external_data = json.loads(response.read().decode('utf-8'))

                external_books = external_data.get('books', [])
                formatted_results = []
                
                for edition_group in external_books:
                    if len(edition_group) == 0:
                        continue
                    book_data = edition_group[0]

                    authors_list = book_data.get('authors', [])
                    author_name = authors_list[0].get('name', 'Unknown') if authors_list else 'Unknown'

                    rating = book_data.get('rating', 'Unknown')
                    rating = rating['average']
                    formatted_results.append({
                        "id": book_data.get('id', 'Unknown'),
                        "title": book_data.get('title', 'Unknown'),
                        "author": author_name,
                        "rating": rating
                    })
                
                self.send_json({"source": "Big Book API", "results": formatted_results})
                return
            
            except urllib.error.HTTPError as he:
                print(f"External API HTTP Error: {he.code} - {he.reason}")
                self.send_json({"detail": "Failed to get records from external book registry"}, status_code = he.code)
                return
            except Exception as e:
                print(f"Error calling external API {e}")
                self.send_json({"detail": "Internal Server Error during external tracking lookup"}, status_code = 500)
                return

        # 4. GET /book/{id}
        else:
            match = re.match(r'^/book/(\d+)$', self.path)
            if match:
                book_id = int(match.group(1))
                try:
                    with psycopg.connect(books_db) as conn:
                        with conn.cursor() as cur:
                            query = "SELECT id, title, author, genre FROM books WHERE id = %s;"
                            cur.execute(query, (book_id,))
                            row = cur.fetchone()

                            if row is None:
                                self.send_json({"detail": f"Book with ID {book_id} not found"}, status_code = 404)
                                return
                            
                            book_data = {
                                "id": row[0],
                                "title": row[1],
                                "author": row[2],
                                "genre": row[3]
                            }
                            self.send_json(book_data)
                            return
                except Exception as e:
                    print(f"Database error encountered: {e}")
                    self.send_json({"detail": "Internal Server Error"}, status_code = 500)
                    return
            
            # If it's not any of the routes above, explicitly return a 404
            self.send_json({"detail": "Not Found"}, status_code = 404)

    def do_POST(self):
        if self.path == '/books':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')

            try:
                data = json.loads(post_data)
                if 'title' not in data or 'author' not in data or 'genre' not in data:
                    self.send_json({"detail": "Missing required fields: title, author, or genre"}, status_code = 400)
                    return
                
                input_title = data["title"]
                input_author = data["author"]
                input_genre = data["genre"]

                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        check_query = """
                            SELECT id FROM books
                            WHERE LOWER(title) = LOWER(%s)
                            AND LOWER(author) = LOWER(%s)
                            AND LOWER(genre) = LOWER(%s);
                        """
                        cur.execute(check_query, (input_title, input_author, input_genre))
                        existing_book = cur.fetchone()

                        if existing_book is not None:
                            self.send_json({"detail": f"The book '{input_title}' already exists in the database."}, status_code = 409)
                            return
                        
                        insert_query = """
                            INSERT INTO books (title, author, genre)
                            VALUES (%s, %s, %s)
                            RETURNING id;
                        """
                        cur.execute(insert_query, (input_title, input_author, input_genre))
                        
                        # get new id
                        result = cur.fetchone()
                        if result is None:
                            print("Error: database executed INSERT but RETURNING id came back empty")
                            self.send_json({"detail": "Database failed to return new book ID"}, status_code = 500)
                            return
                        new_id = result[0]

                        new_book = {
                            "id": new_id,
                            "title": input_title,
                            "author": input_author,
                            "genre": input_genre
                        }

                        self.send_json(new_book, status_code = 201)
                        return
            except json.JSONDecodeError:
                self.send_json({"detail": "Invalid JSON format in request body"}, status_code = 400)
                return
            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal Server Error"}, status_code = 500)
                return
            
        if self.path == '/recommend' or self.path == '/search-external' or re.match(r'^/book/(\d+)$', self.path):
            self.send_json({"detail": "Method Not Allowed. Use GET instead."}, status_code = 405)
            return
        

        self.send_json({"detail": "Not Found"}, status_code = 404)

    
    def do_DELETE(self):
        if self.path == '/books':
            content_length = int(self.headers['Content-Length'])
            delete_data = self.rfile.read(content_length).decode('utf-8')

            try:
                data = json.loads(delete_data)
                if 'title' not in data or 'author' not in data or 'genre' not in data:
                    self.send_json({"detail": "Missing required fields to delete: title, author, or genre"}, status_code = 400)
                    return
                input_title = data["title"]
                input_author = data["author"]
                input_genre = data["genre"]

                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        delete_query = """
                            DELETE FROM books
                            WHERE LOWER(title) = LOWER(%s)
                            AND LOWER(author) = LOWER(%s)
                            AND LOWER(genre) = LOWER(%s);
                        """
                        cur.execute(delete_query, (input_title, input_author, input_genre))
                        deleted_rows_count = cur.rowcount

                        if deleted_rows_count == 0:
                            self.send_json({"detail": "No matching books found to delete."}, status_code = 404)
                            return
                        self.send_json({"detail": f"Successfully deleted {deleted_rows_count} books matching your criteria."}, status_code = 200)

            except json.JSONDecodeError:
                self.send_json({"detail": "Invalid JSON format in request body"}, status_code = 400)
            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal server Error"}, status_code = 500)
                return
        
        if self.path == '/recommend' or self.path == '/search-external' or re.match(r'^/book/(\d+)$', self.path):
            self.send_json({"detail": "Method Not Allowed. Use GET instead."}, status_code = 405)
            return
        
        self.send_json({"detail": "Not Found"}, status_code = 404)


def run(server_class = HTTPServer, handler_class = bookRequestsHandler, port = 8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Server successfully running on http://localhost:{port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()

if __name__ == '__main__':
    run()
